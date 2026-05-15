# -----------------------------
# Concentration-screen Opentrons helper functions
# -----------------------------

from __future__ import annotations

import logging
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

_RUN_LOGGER = logging.getLogger(__name__)
_RUN_LOGGER.setLevel(logging.INFO)
_RUN_LOGGER.propagate = False
_RUN_LOG_FILE = None

# Default liquid-handling parameters. These can be overridden from the notebook.
DEFAULT_MAX_DISPENSE = 200          # uL; two thirds of the P300 volume
DEFAULT_PRE_WET_CYCLES = 3
DEFAULT_PRE_WET_VOLUME = 180        # uL
DEFAULT_AIR_GAP_VOLUME = 15         # uL
DEFAULT_TIP_CHANGE_INTERVAL = 3     # used in accurate mode
DEFAULT_TRANSFER_MODE = "fast"      # "fast" = one tip per reagent/source; "accurate" = change tips regularly

# Optional robot movement-speed settings.
# These control gantry/plunger movement speed rather than liquid flow rate.
DEFAULT_PIPETTE_DEFAULT_SPEED = 400  # mm/s
DEFAULT_MAX_HEAD_SPEED = 400         # mm/s for X/Y/Z/A axes when supported
DEFAULT_TOUCH_TIP_SPEED = 40         # mm/s; keep rim-touching slow to avoid droplet flicking
DEFAULT_TOUCH_TIP_RADIUS = 0.75      # fraction of well radius; avoid full-edge moves in large source wells
DEFAULT_TOUCH_TIP_V_OFFSET = -2      # mm below well top
DEFAULT_TOUCH_TIP_SAFETY_MARGIN = 3  # mm inside the estimated OT-2 XY travel envelope
DEFAULT_OT2_X_LIMITS = (0, 418)      # mm, conservative deck-coordinate limits
DEFAULT_OT2_Y_LIMITS = (0, 356)      # mm, conservative deck-coordinate limits

VALID_48_WELL_ROWS = tuple("ABCDEF")
VALID_48_WELL_COLUMNS = range(1, 9)
P300_FALLBACK_MAX_VOLUME = 300

DIAMINE_VOLUME_KEY = "diamine_volume_uL"
DIALDEHYDE_VOLUME_KEY = "dialdehyde_volume_uL"
CHLOROFORM_TOP_UP_VOLUME_KEY = "chloroform_top_up_volume_uL"


def _pipette_max_volume(pipette: Any) -> float:
    """Return the pipette capacity, falling back to P300 capacity for simulation-like objects."""
    try:
        return float(getattr(pipette, "max_volume", P300_FALLBACK_MAX_VOLUME))
    except (TypeError, ValueError):
        return float(P300_FALLBACK_MAX_VOLUME)


def _drop_tip_or_preserve_original_error(
    pipette: Any,
    protocol: Optional[Any],
    success_message: str,
) -> None:
    """Drop a cleanup tip without masking an earlier robot/protocol exception."""
    active_exception = sys.exc_info()[0] is not None
    try:
        pipette.drop_tip()
    except Exception as exc:
        log_step(protocol, f"Warning: failed to drop tip during cleanup: {exc}")
        if not active_exception:
            raise
    else:
        log_step(protocol, success_message)


def estimate_touch_tip_xy_bounds(well: Any, touch_tip_radius: float) -> dict[str, float]:
    """Estimate the XY deck bounds requested by touch_tip() for one well."""
    if not 0 < touch_tip_radius <= 1:
        raise ValueError("touch_tip_radius must be > 0 and <= 1.")

    center = well.top().point
    diameter = getattr(well, "diameter", None)
    if diameter is not None:
        x_reach = y_reach = float(diameter) * touch_tip_radius / 2
    else:
        x_reach = float(well.length) * touch_tip_radius / 2
        y_reach = float(well.width) * touch_tip_radius / 2

    return {
        "x_min": center.x - x_reach,
        "x_max": center.x + x_reach,
        "y_min": center.y - y_reach,
        "y_max": center.y + y_reach,
    }


def validate_touch_tip_clearance(
    wells: list[Any],
    touch_tip_radius: float = DEFAULT_TOUCH_TIP_RADIUS,
    x_limits: tuple[float, float] = DEFAULT_OT2_X_LIMITS,
    y_limits: tuple[float, float] = DEFAULT_OT2_Y_LIMITS,
    safety_margin_mm: float = DEFAULT_TOUCH_TIP_SAFETY_MARGIN,
) -> None:
    """Fail before running if an estimated touch-tip path may exceed OT-2 XY limits."""
    if safety_margin_mm < 0:
        raise ValueError("safety_margin_mm must be non-negative.")

    x_min_limit, x_max_limit = x_limits
    y_min_limit, y_max_limit = y_limits
    for well in wells:
        bounds = estimate_touch_tip_xy_bounds(well, touch_tip_radius)
        if (
            bounds["x_min"] < x_min_limit + safety_margin_mm
            or bounds["x_max"] > x_max_limit - safety_margin_mm
            or bounds["y_min"] < y_min_limit + safety_margin_mm
            or bounds["y_max"] > y_max_limit - safety_margin_mm
        ):
            raise ValueError(
                f"{well} touch-tip path may exceed OT-2 XY travel limits. "
                f"Estimated bounds: {bounds}. Reduce TOUCH_TIP_RADIUS, move the labware, "
                "or disable source touch-tip for this deck layout."
            )


def _normalise_48_well_row(row: Any) -> str:
    if not isinstance(row, str):
        raise ValueError("48-well plate row names must be strings A-F.")

    normalised_row = row.strip().upper()
    if normalised_row not in VALID_48_WELL_ROWS:
        raise ValueError("48-well plate rows must be A-F.")

    return normalised_row


def _validate_48_well_column(column: Any) -> int:
    if not isinstance(column, int) or isinstance(column, bool):
        raise ValueError("48-well plate columns must be integers 1-8.")

    if column not in VALID_48_WELL_COLUMNS:
        raise ValueError("48-well plate columns must be 1-8.")

    return column


def _normalise_48_well_name(well_name: Any) -> str:
    if not isinstance(well_name, str) or len(well_name.strip()) < 2:
        raise ValueError("Well names must look like A1 through F8.")

    cleaned_name = well_name.strip().upper()
    row = _normalise_48_well_row(cleaned_name[0])
    try:
        column = int(cleaned_name[1:])
    except ValueError as exc:
        raise ValueError("Well names must look like A1 through F8.") from exc

    column = _validate_48_well_column(column)
    normalised_name = f"{row}{column}"
    if cleaned_name != normalised_name:
        raise ValueError("Well names must use the canonical format A1 through F8.")

    return normalised_name


def setup_run_logger(
    log_file: Optional[str] = None,
    log_dir: str = "opentrons_logs",
    log_prefix: str = "opentrons_run_log",
    echo: bool = True,
) -> str:
    """Configure one notebook-safe text log file for this protocol run."""
    global _RUN_LOG_FILE

    if log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = Path(log_dir) / f"{log_prefix}_{timestamp}.txt"
    else:
        log_path = Path(log_file)

    log_path = log_path.expanduser()
    if not log_path.is_absolute():
        log_path = Path.cwd() / log_path
    log_path = log_path.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    for handler in list(_RUN_LOGGER.handlers):
        if isinstance(handler, logging.FileHandler):
            _RUN_LOGGER.removeHandler(handler)
            handler.close()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _RUN_LOGGER.addHandler(file_handler)
    _RUN_LOGGER.propagate = False
    _RUN_LOG_FILE = str(log_path)

    if echo:
        print(f"Run log file: {_RUN_LOG_FILE}")

    return _RUN_LOG_FILE


def get_run_log_path() -> Optional[str]:
    return _RUN_LOG_FILE


def log_step(protocol: Optional[Any], message: str, echo: bool = True) -> None:
    """Print a message in the notebook, text run log, and Opentrons protocol log."""
    if echo:
        print(message)
    if _RUN_LOGGER.handlers:
        try:
            _RUN_LOGGER.info(message)
        except Exception as exc:
            if echo:
                print(f"Warning: failed to write to run log: {exc}")
    if protocol is not None:
        protocol.comment(message)


def log_absolute_time_tick(
    protocol: Optional[Any],
    message: str,
    echo: bool = True,
    include_date: bool = True,
) -> str:
    """Print/log the absolute wall-clock timestamp and return it as a string."""
    timestamp_format = "%Y-%m-%d %H:%M:%S" if include_date else "%H:%M:%S"
    timestamp = datetime.now().strftime(timestamp_format)
    log_step(protocol, f"[{timestamp}] {message}", echo=echo)
    return timestamp


def set_robot_speeds(
    protocol: Any,
    pipette: Any,
    pipette_default_speed: float = DEFAULT_PIPETTE_DEFAULT_SPEED,
    echo: bool = True,
) -> None:
    """
    Set movement-speed limits for the OT-2 where supported.

    This is separate from aspirate/dispense flow rates:
    - pipette.flow_rate controls liquid transfer speed;
    - pipette.default_speed controls robot movement speed.
    """
    if pipette_default_speed <= 0:
        raise ValueError("pipette_default_speed must be positive.")

    if hasattr(pipette, "default_speed"):
        pipette.default_speed = pipette_default_speed
        log_step(protocol, f"Set pipette default movement speed to {pipette_default_speed} mm/s.", echo=echo)
    else:
        log_step(protocol, "Warning: this pipette object does not expose default_speed.", echo=echo)

def calculate_dispense(
    total_vol: float,
    max_dispense: float = DEFAULT_MAX_DISPENSE,
    equal_dispenses: bool = False,
) -> list[float]:
    """Split a total volume into one or more pipette dispenses."""
    if total_vol < 0:
        raise ValueError("total_vol must be non-negative.")
    if max_dispense <= 0:
        raise ValueError("max_dispense must be positive.")
    if total_vol == 0:
        return []

    if not equal_dispenses:
        dispenses = [float(max_dispense) for _ in range(int(total_vol // max_dispense))]
        remainder = total_vol % max_dispense
        if remainder != 0:
            dispenses.append(round(float(remainder), 2))
        return dispenses

    num_parts = math.ceil(total_vol / max_dispense)
    base_value = round(total_vol / num_parts, 2)
    dispenses = [base_value for _ in range(num_parts - 1)]
    last_value = round(total_vol - sum(dispenses), 2)
    return dispenses + [last_value]


def validate_volume_for_p300(
    volume: float,
    name: str,
    min_reliable_volume: float = 30,
    max_single_dispense: float = DEFAULT_MAX_DISPENSE,
    allow_zero: bool = False,
    allow_below_min: bool = False,
    protocol: Optional[Any] = None,
) -> None:
    """Validate a per-well volume for P300 handling and report split-dispense warnings."""
    if volume < 0:
        raise ValueError(f"{name} volume is {volume} uL. Please enter a non-negative volume.")

    if volume == 0:
        if allow_zero:
            return
        raise ValueError(f"{name} volume is 0 uL. Please enter a positive volume before running.")

    if volume < min_reliable_volume and not allow_below_min:
        raise ValueError(
            f"{name} volume is {volume} uL. Use >= {min_reliable_volume} uL for more reliable P300 handling."
        )

    if volume > max_single_dispense:
        log_step(
            protocol,
            f"Warning: {name} volume is {volume} uL and will be split into multiple dispenses of <= {max_single_dispense} uL.",
        )


def build_replicate_wells(
    rows: Iterable[str],
    column: int,
) -> list[str]:
    """
    Build replicate wells for one concentration condition.

    Example:
    rows A-C, column 1 -> A1, B1, C1.
    """
    validated_rows = [_normalise_48_well_row(row) for row in rows]
    if len(validated_rows) == 0:
        raise ValueError("At least one replicate row is required.")

    validated_column = _validate_48_well_column(column)
    return [f"{row}{validated_column}" for row in validated_rows]


def build_concentration_screen_plate_map(
    concentration_conditions: dict[str, dict[str, Any]],
    conditions_to_run: list[str],
    replicate_rows: Iterable[str],
    start_column: int = 1,
    protocol: Optional[Any] = None,
) -> dict[str, dict[str, Any]]:
    """
    Build and validate the target-well map for a concentration screen.

    Each condition is assigned a column by default, so triplicates use rows such as
    A/B/C across that column: conc_1 -> A1/B1/C1, conc_2 -> A2/B2/C2.

    Future layout edits can either:
    - change `replicate_rows`;
    - set a per-condition `column`;
    - set explicit per-condition `target_wells`.
    """
    if len(conditions_to_run) == 0:
        raise ValueError("Select at least one concentration condition to run.")

    default_rows = [_normalise_48_well_row(row) for row in replicate_rows]
    start_column = _validate_48_well_column(start_column)

    used_wells = []
    plate_map = {}

    for condition_index, condition_name in enumerate(conditions_to_run):
        if condition_name not in concentration_conditions:
            raise ValueError(f"Unknown concentration condition: {condition_name}")

        condition = concentration_conditions[condition_name]
        condition_rows = condition.get("replicate_rows", default_rows)
        condition_rows = [_normalise_48_well_row(row) for row in condition_rows]

        if "target_wells" in condition:
            target_wells = [_normalise_48_well_name(well) for well in condition["target_wells"]]
        else:
            column = condition.get("column", start_column + condition_index)
            target_wells = build_replicate_wells(rows=condition_rows, column=column)

        for required_key in [DIAMINE_VOLUME_KEY, DIALDEHYDE_VOLUME_KEY, CHLOROFORM_TOP_UP_VOLUME_KEY]:
            if required_key not in condition:
                raise ValueError(f"{condition_name} must define {required_key}.")

        plate_map[condition_name] = {
            **condition,
            "condition": condition_name,
            "replicate_rows": condition_rows,
            "target_wells": target_wells,
            DIAMINE_VOLUME_KEY: float(condition[DIAMINE_VOLUME_KEY]),
            DIALDEHYDE_VOLUME_KEY: float(condition[DIALDEHYDE_VOLUME_KEY]),
            CHLOROFORM_TOP_UP_VOLUME_KEY: float(condition[CHLOROFORM_TOP_UP_VOLUME_KEY]),
        }
        used_wells.extend(target_wells)

        log_step(protocol, f"Condition: {condition_name} -> {', '.join(target_wells)}")

    if len(set(used_wells)) != len(used_wells):
        raise ValueError("Duplicate target wells detected. Check concentration-condition well assignments.")

    return plate_map


def make_well_volume_map(
    plate_map: dict[str, dict[str, Any]],
    volume_key: str,
) -> dict[str, float]:
    """Build an ordered well -> volume map for one reagent across all conditions."""
    well_volumes = {}
    for condition in plate_map.values():
        volume = float(condition[volume_key])
        for well_name in condition["target_wells"]:
            well_volumes[well_name] = volume
    return well_volumes


def make_condition_by_well_map(plate_map: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Build a well -> concentration condition lookup for dispense records."""
    condition_by_well = {}
    for condition_name, condition in plate_map.items():
        for well_name in condition["target_wells"]:
            condition_by_well[well_name] = condition_name
    return condition_by_well


def pre_wet_source(
    pipette: Any,
    protocol: Any,
    source_well: Any,
    cycles: int = DEFAULT_PRE_WET_CYCLES,
    volume: float = DEFAULT_PRE_WET_VOLUME,
    delay_seconds: float = 5,
    touch_tip_speed: float = DEFAULT_TOUCH_TIP_SPEED,
    touch_tip_radius: float = DEFAULT_TOUCH_TIP_RADIUS,
    touch_tip_v_offset: float = DEFAULT_TOUCH_TIP_V_OFFSET,
) -> None:
    """Pre-wet the current pipette tip using the correct reagent source, with slow rim-touching."""
    if cycles < 0:
        raise ValueError("cycles must be non-negative.")
    if volume <= 0:
        raise ValueError("pre-wet volume must be positive.")
    if touch_tip_speed <= 0:
        raise ValueError("touch_tip_speed must be positive.")
    validate_touch_tip_clearance([source_well], touch_tip_radius=touch_tip_radius)

    pipette_capacity = _pipette_max_volume(pipette)
    if volume > pipette_capacity:
        raise ValueError(f"pre-wet volume must not exceed pipette capacity ({pipette_capacity:g} uL).")

    for _ in range(cycles):
        pipette.aspirate(volume, source_well)
        protocol.delay(seconds=delay_seconds)
        pipette.dispense(volume, source_well.bottom())

    original_default_speed = getattr(pipette, "default_speed", None)
    try:
        if original_default_speed is not None:
            pipette.default_speed = touch_tip_speed

        pipette.touch_tip(
            source_well,
            radius=touch_tip_radius,
            v_offset=touch_tip_v_offset,
            speed=touch_tip_speed,
        )
    finally:
        if original_default_speed is not None:
            pipette.default_speed = original_default_speed

    pipette.blow_out(source_well.top())


def transfer_with_split_dispenses(
    pipette: Any,
    source_well: Any,
    target_well: Any,
    total_volume: float,
    dispense_rate: float,
    max_dispense: float = DEFAULT_MAX_DISPENSE,
    air_gap_volume: float = DEFAULT_AIR_GAP_VOLUME,
) -> None:
    """Transfer a volume using split dispenses, with air gap and blow-out for each split dispense."""
    if total_volume <= 0:
        raise ValueError("total_volume must be positive.")
    if max_dispense <= 0:
        raise ValueError("max_dispense must be positive.")
    if air_gap_volume < 0:
        raise ValueError("air_gap_volume must be non-negative.")

    pipette_capacity = _pipette_max_volume(pipette)
    if max_dispense + air_gap_volume > pipette_capacity:
        raise ValueError(
            f"max_dispense plus air_gap_volume exceeds pipette capacity ({pipette_capacity:g} uL)."
        )

    pipette.flow_rate.dispense = dispense_rate

    for amount in calculate_dispense(total_vol=total_volume, max_dispense=max_dispense, equal_dispenses=True):
        if amount <= 0:
            continue
        if amount + air_gap_volume > pipette_capacity:
            raise ValueError(
                f"split dispense volume plus air gap exceeds pipette capacity ({pipette_capacity:g} uL)."
            )

        pipette.aspirate(amount, source_well)
        if air_gap_volume > 0:
            pipette.air_gap(air_gap_volume)
        pipette.dispense(amount + air_gap_volume, target_well.top())
        pipette.blow_out(target_well.top())


def dispense_to_wells_with_tip_changes(
    pipette: Any,
    protocol: Any,
    plate: Any,
    source_well: Any,
    target_well_volumes: dict[str, float],
    dispense_rate: float,
    reagent_name: str,
    tip_change_interval: int = DEFAULT_TIP_CHANGE_INTERVAL,
    transfer_mode: str = DEFAULT_TRANSFER_MODE,
    max_dispense: float = DEFAULT_MAX_DISPENSE,
    air_gap_volume: float = DEFAULT_AIR_GAP_VOLUME,
    pre_wet_cycles: int = DEFAULT_PRE_WET_CYCLES,
    pre_wet_volume: float = DEFAULT_PRE_WET_VOLUME,
    touch_tip_speed: float = DEFAULT_TOUCH_TIP_SPEED,
    touch_tip_radius: float = DEFAULT_TOUCH_TIP_RADIUS,
    touch_tip_v_offset: float = DEFAULT_TOUCH_TIP_V_OFFSET,
    condition_by_well: Optional[dict[str, str]] = None,
    log_each_dispense_time: bool = False,
    log_absolute_time: bool = False,
) -> list[dict[str, Any]]:
    """
    Dispense one reagent/source to a well -> volume map.

    transfer_mode="fast" uses one tip for all positive-volume target wells for this source.
    transfer_mode="accurate" changes tip after `tip_change_interval` target wells.
    """
    if tip_change_interval <= 0:
        raise ValueError("tip_change_interval must be positive.")
    if transfer_mode not in {"accurate", "fast"}:
        raise ValueError('transfer_mode must be either "accurate" or "fast".')

    positive_target_well_volumes = {
        _normalise_48_well_name(well_name): float(volume)
        for well_name, volume in target_well_volumes.items()
        if float(volume) > 0
    }
    if len(positive_target_well_volumes) == 0:
        log_step(protocol, f"Skipping {reagent_name}: all target volumes are 0 uL.")
        return []
    validate_touch_tip_clearance([source_well], touch_tip_radius=touch_tip_radius)

    if transfer_mode == "fast":
        effective_tip_change_interval = len(positive_target_well_volumes)
        log_step(protocol, f"Fast mode enabled for {reagent_name}: using one tip for this reagent/source.")
    else:
        effective_tip_change_interval = tip_change_interval
        log_step(
            protocol,
            f"Accurate mode enabled for {reagent_name}: changing tip after every "
            f"{effective_tip_change_interval} target-well dispense(s).",
        )

    condition_by_well = condition_by_well or {}
    has_tip = False
    dispenses_since_tip_change = 0
    tip_number_for_reagent = 0
    dispense_records = []

    try:
        for dispense_order_index, (well_name, volume) in enumerate(positive_target_well_volumes.items(), start=1):
            if not has_tip:
                pipette.pick_up_tip()
                has_tip = True
                tip_number_for_reagent += 1
                dispenses_since_tip_change = 0
                log_step(protocol, f"Picked up tip {tip_number_for_reagent} for {reagent_name}.")
                pre_wet_source(
                    pipette=pipette,
                    protocol=protocol,
                    source_well=source_well,
                    cycles=pre_wet_cycles,
                    volume=pre_wet_volume,
                    touch_tip_speed=touch_tip_speed,
                    touch_tip_radius=touch_tip_radius,
                    touch_tip_v_offset=touch_tip_v_offset,
                )

            start_timestamp = None
            finish_timestamp = None

            if log_each_dispense_time and log_absolute_time:
                start_timestamp = log_absolute_time_tick(
                    protocol,
                    f"Starting dispense: {volume} uL {reagent_name} to {well_name}.",
                )
            else:
                log_step(protocol, f"Adding {volume} uL {reagent_name} to {well_name}.")

            transfer_with_split_dispenses(
                pipette=pipette,
                source_well=source_well,
                target_well=plate[well_name],
                total_volume=volume,
                dispense_rate=dispense_rate,
                max_dispense=max_dispense,
                air_gap_volume=air_gap_volume,
            )

            if log_each_dispense_time and log_absolute_time:
                finish_timestamp = log_absolute_time_tick(
                    protocol,
                    f"Finished dispense: {volume} uL {reagent_name} to {well_name}.",
                )

            dispense_records.append({
                "well": well_name,
                "condition": condition_by_well.get(well_name),
                "dispense_order_index": dispense_order_index,
                "reagent_name": reagent_name,
                "total_volume_uL": volume,
                "start_timestamp": start_timestamp,
                "finish_timestamp": finish_timestamp,
            })

            dispenses_since_tip_change += 1
            if dispenses_since_tip_change == effective_tip_change_interval:
                pipette.drop_tip()
                has_tip = False
                log_step(
                    protocol,
                    f"Dropped tip {tip_number_for_reagent} after {effective_tip_change_interval} "
                    f"target-well dispense(s) for {reagent_name}.",
                )
    finally:
        if has_tip:
            _drop_tip_or_preserve_original_error(
                pipette=pipette,
                protocol=protocol,
                success_message=(
                    f"Dropped final tip {tip_number_for_reagent} for {reagent_name} after "
                    f"{dispenses_since_tip_change} target-well dispense(s)."
                ),
            )

    return dispense_records


def summarise_concentration_screen_records(
    plate_map: dict[str, dict[str, Any]],
    dispense_records_by_reagent: dict[str, list[dict[str, Any]]],
    protocol: Optional[Any] = None,
    echo: bool = True,
) -> list[dict[str, Any]]:
    """Summarise concentration-screen conditions and dispense record counts."""
    summary = []
    for condition_name, condition in plate_map.items():
        summary_record = {
            "condition": condition_name,
            "label": condition.get("label"),
            "nominal_concentration": condition.get("nominal_concentration"),
            "wells": condition["target_wells"],
            DIAMINE_VOLUME_KEY: condition[DIAMINE_VOLUME_KEY],
            DIALDEHYDE_VOLUME_KEY: condition[DIALDEHYDE_VOLUME_KEY],
            CHLOROFORM_TOP_UP_VOLUME_KEY: condition[CHLOROFORM_TOP_UP_VOLUME_KEY],
        }
        summary.append(summary_record)
        log_step(
            protocol,
            (
                f"Concentration condition {condition_name}: {', '.join(condition['target_wells'])} | "
                f"diamine {condition[DIAMINE_VOLUME_KEY]} uL, "
                f"chloroform top-up {condition[CHLOROFORM_TOP_UP_VOLUME_KEY]} uL, "
                f"dialdehyde {condition[DIALDEHYDE_VOLUME_KEY]} uL."
            ),
            echo=echo,
        )

    for reagent_name, records in dispense_records_by_reagent.items():
        log_step(protocol, f"{reagent_name}: {len(records)} recorded target-well dispense(s).", echo=echo)

    return summary
