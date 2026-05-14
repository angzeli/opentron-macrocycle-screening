# -----------------------------
# Kinetic Opentrons helper functions
# -----------------------------

from __future__ import annotations

import math
import time
from datetime import datetime
from numbers import Real
from typing import Any, Optional

# Default liquid-handling parameters. These can be overridden from the notebook.
DEFAULT_MAX_DISPENSE = 200          # uL; two thirds of the P300 volume
DEFAULT_PRE_WET_CYCLES = 3
DEFAULT_PRE_WET_VOLUME = 180        # uL
DEFAULT_AIR_GAP_VOLUME = 15         # uL
DEFAULT_TIP_CHANGE_INTERVAL = 3     # discard tip after every 3 target-well dispenses
DEFAULT_TRANSFER_MODE = "accurate"  # fast mode = one tip + one pre-wet per reagent/solvent source
                                    # accurate mode = repeated tip changes + repeated pre-wets

# Optional robot movement-speed settings.
# These control gantry/plunger movement speed rather than liquid flow rate.
DEFAULT_PIPETTE_DEFAULT_SPEED = 400  # mm/s; Opentrons default is often conservative
DEFAULT_MAX_HEAD_SPEED = 400         # mm/s for X/Y/Z/A axes when supported
DEFAULT_TOUCH_TIP_SPEED = 40         # mm/s; keep rim-touching slow to avoid splashing/droplet flicking

P300_FALLBACK_MAX_VOLUME = 300


def _pipette_max_volume(pipette: Any) -> float:
    """Return the pipette capacity, falling back to P300 capacity for simulation-like objects."""
    try:
        return float(getattr(pipette, "max_volume", P300_FALLBACK_MAX_VOLUME))
    except (TypeError, ValueError):
        return float(P300_FALLBACK_MAX_VOLUME)


def set_robot_speeds(
    protocol: Any,
    pipette: Any,
    pipette_default_speed: float = DEFAULT_PIPETTE_DEFAULT_SPEED,
    max_head_speed: float = DEFAULT_MAX_HEAD_SPEED,
    echo: bool = True,
) -> None:
    """
    Set movement-speed limits for the OT-2 where supported.

    This is separate from aspirate/dispense flow rates:
    - pipette.flow_rate controls liquid transfer speed;
    - pipette.default_speed and protocol.max_speeds control robot movement speed.
    """
    if pipette_default_speed <= 0:
        raise ValueError("pipette_default_speed must be positive.")

    if max_head_speed <= 0:
        raise ValueError("max_head_speed must be positive.")

    # Controls how fast the pipette moves between locations.
    if hasattr(pipette, "default_speed"):
        pipette.default_speed = pipette_default_speed
        log_step(protocol, f"Set pipette default movement speed to {pipette_default_speed} mm/s.", echo=echo)
    else:
        log_step(protocol, "Warning: this pipette object does not expose default_speed.", echo=echo)

    # Controls maximum gantry/head speed for each axis where supported by the API context.
    if hasattr(protocol, "max_speeds"):
        for axis in ["X", "Y", "Z", "A"]:
            protocol.max_speeds[axis] = max_head_speed
        log_step(protocol, f"Set protocol max head speed to {max_head_speed} mm/s for X/Y/Z/A axes.", echo=echo)
    else:
        log_step(protocol, "Warning: this protocol context does not expose max_speeds.", echo=echo)

def log_step(protocol, message: str, echo: bool = True) -> None:
    if echo:
        print(message)
    if protocol is not None:
        protocol.comment(message)


def start_timer(protocol: Optional[Any] = None, label: str = "Experiment timer", echo: bool = True) -> float:
    """Start a monotonic timer and print/log the start tick."""
    start_time = time.monotonic()
    log_step(protocol, f"{label} started at t = 0.00 s.", echo=echo)
    return start_time


def log_time_tick(
    protocol: Optional[Any],
    start_time: float,
    message: str,
    echo: bool = True,
) -> float:
    """Print/log the elapsed time since start_time and return the elapsed time in seconds."""
    elapsed = time.monotonic() - start_time
    log_step(protocol, f"[t = {elapsed:8.2f} s | {elapsed / 60:6.2f} min] {message}", echo=echo)
    return elapsed

def log_absolute_time_tick(
    protocol,
    message: str,
    echo: bool = True,
    include_date: bool = True,
) -> str:
    """Print/log the absolute wall-clock timestamp and return it as a string."""
    timestamp_format = "%Y-%m-%d %H:%M:%S" if include_date else "%H:%M:%S"
    timestamp = datetime.now().strftime(timestamp_format)
    log_step(protocol, f"[{timestamp}] {message}", echo=echo)
    return timestamp

def calculate_dispense(total_vol: float, max_dispense: float = DEFAULT_MAX_DISPENSE, equal_dispenses: bool = False) -> list[float]:
    """
    Split a total volume into multiple pipette dispenses.

    If equal_dispenses=False, use as many max_dispense chunks as possible,
    followed by a smaller remainder.

    If equal_dispenses=True, split the total volume into near-equal chunks,
    each no larger than max_dispense.
    """
    if total_vol < 0:
        raise ValueError("total_vol must be non-negative.")

    if max_dispense <= 0:
        raise ValueError("max_dispense must be positive.")

    if total_vol == 0:
        return []

    if not equal_dispenses:
        disp = [float(max_dispense) for _ in range(int(total_vol // max_dispense))]
        remainder = total_vol % max_dispense
        if remainder != 0:
            disp.append(round(float(remainder), 2))
        return disp

    num_parts = math.ceil(total_vol / max_dispense)
    base_value = round(total_vol / num_parts, 2)
    disp = [base_value for _ in range(num_parts - 1)]
    last_value = round(total_vol - sum(disp), 2)
    return disp + [last_value]

def build_vertical_triplicate_wells(timepoint_index: int) -> list[str]:
    """
    Build vertical triplicate wells for one kinetic time point.

    Time points 1-8 use rows A/B/C across columns 1-8.
    Time points 9-16 use rows D/E/F across columns 1-8.
    """
    if not isinstance(timepoint_index, int) or isinstance(timepoint_index, bool):
        raise ValueError("timepoint_index must be an integer between 1 and 16.")

    if not 1 <= timepoint_index <= 16:
        raise ValueError("timepoint_index must be between 1 and 16 inclusive.")

    if timepoint_index <= 8:
        rows = ["A", "B", "C"]
        column = timepoint_index
    else:
        rows = ["D", "E", "F"]
        column = timepoint_index - 8

    return [f"{row}{column}" for row in rows]


def build_timepoint_map(timepoints_min: list[float]) -> list[dict[str, Any]]:
    """
    Build an explicit 48-well kinetic time-point map.

    Each entry contains:
    - time_min: the nominal kinetic time point
    - timepoint_index: the 1-based vertical triplicate block index
    - plate_region: upper for A/B/C blocks or lower for D/E/F blocks
    - wells: replicate wells for that time point
    """
    if len(timepoints_min) == 0:
        raise ValueError("At least one time point is required.")

    if len(timepoints_min) > 16:
        raise ValueError("TIMEPOINTS_MIN must contain no more than 16 time points for the vertical triplicate 48-well layout.")

    for time_min in timepoints_min:
        if not isinstance(time_min, Real) or isinstance(time_min, bool):
            raise ValueError("TIMEPOINTS_MIN values must be numeric minute labels.")
        if time_min < 0:
            raise ValueError("TIMEPOINTS_MIN values must be non-negative.")

    if len(set(timepoints_min)) != len(timepoints_min):
        raise ValueError(
            "TIMEPOINTS_MIN contains duplicate nominal time values. "
            "Use unique nominal time labels for robust kinetic summaries."
        )

    timepoint_map = []
    for timepoint_index, time_min in enumerate(timepoints_min, start=1):
        timepoint_map.append({
            "time_min": time_min,
            "timepoint_index": timepoint_index,
            "plate_region": "upper" if timepoint_index <= 8 else "lower",
            "wells": build_vertical_triplicate_wells(timepoint_index),
        })

    return timepoint_map


def flatten_timepoint_wells(timepoint_map: list[dict[str, Any]]) -> list[str]:
    """Flatten a kinetic time-point map into target-well order and reject duplicates."""
    target_wells = []
    for entry in timepoint_map:
        target_wells.extend(entry["wells"])

    if len(set(target_wells)) != len(target_wells):
        raise ValueError("Duplicate target wells detected in timepoint map.")

    return target_wells


def validate_volume_for_p300(
    volume: float,
    name: str,
    min_reliable_volume: float = 30,
    max_single_dispense: float = DEFAULT_MAX_DISPENSE,
    protocol: Optional[Any] = None,
) -> None:
    """Validate volumes for P300 handling and optionally report split-dispense warnings."""
    if volume <= 0:
        raise ValueError(f"{name} volume is {volume} uL. Please enter a positive volume before running.")

    if volume < min_reliable_volume:
        raise ValueError(
            f"{name} volume is {volume} uL. Use >= {min_reliable_volume} uL for more reliable P300 handling."
        )

    if volume > max_single_dispense:
        message = (
            f"Warning: {name} volume is {volume} uL and will be split into multiple "
            f"dispenses of <= {max_single_dispense} uL."
        )
        if protocol is not None:
            log_step(protocol, message)
        else:
            print(message)


def pre_wet_source(
    pipette: Any,
    protocol: Any,
    source_well: Any,
    cycles: int = DEFAULT_PRE_WET_CYCLES,
    volume: float = DEFAULT_PRE_WET_VOLUME,
    delay_seconds: float = 5,
    touch_tip_speed: float = DEFAULT_TOUCH_TIP_SPEED,
) -> None:
    """Pre-wet the current pipette tip using the correct reagent source.

    Rim-touching is deliberately performed slowly because fast touch-tip movement
    can flick droplets, disturb volatile solvent droplets, or create splashing near
    the source well.
    """
    if cycles < 0:
        raise ValueError("cycles must be non-negative.")

    if volume <= 0:
        raise ValueError("pre-wet volume must be positive.")

    if touch_tip_speed <= 0:
        raise ValueError("touch_tip_speed must be positive.")

    pipette_capacity = _pipette_max_volume(pipette)
    if volume > pipette_capacity:
        raise ValueError(f"pre-wet volume must not exceed pipette capacity ({pipette_capacity:g} uL).")

    for _ in range(cycles):
        pipette.aspirate(volume, source_well)
        protocol.delay(seconds=delay_seconds)
        pipette.dispense(volume, source_well.bottom())

    # Temporarily slow down robot movement only for rim-touching, then restore it.
    original_default_speed = getattr(pipette, "default_speed", None)
    try:
        if original_default_speed is not None:
            pipette.default_speed = touch_tip_speed

        pipette.touch_tip(source_well)
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

    for amount in calculate_dispense(
        total_vol=total_volume,
        max_dispense=max_dispense,
        equal_dispenses=True,
    ):
        if amount <= 0:
            continue
        if amount + air_gap_volume > pipette_capacity:
            raise ValueError(
                f"split dispense volume plus air gap exceeds pipette capacity ({pipette_capacity:g} uL)."
            )

        pipette.aspirate(amount, source_well)
        pipette.air_gap(air_gap_volume)
        pipette.dispense(amount + air_gap_volume, target_well.top())
        pipette.blow_out(target_well.top())


def dispense_to_wells_with_tip_changes(
    pipette: Any,
    protocol: Any,
    plate: Any,
    source_well: Any,
    target_well_names: list[str],
    total_volume: float,
    dispense_rate: float,
    reagent_name: str,
    tip_change_interval: int = DEFAULT_TIP_CHANGE_INTERVAL,
    transfer_mode: str = DEFAULT_TRANSFER_MODE,
    max_dispense: float = DEFAULT_MAX_DISPENSE,
    air_gap_volume: float = DEFAULT_AIR_GAP_VOLUME,
    pre_wet_cycles: int = DEFAULT_PRE_WET_CYCLES,
    pre_wet_volume: float = DEFAULT_PRE_WET_VOLUME,
    touch_tip_speed: float = DEFAULT_TOUCH_TIP_SPEED,
    timer_start: Optional[float] = None,
    log_each_dispense_time: bool = True,
    log_absolute_time: bool = True,
) -> list[dict[str, Any]]:
    """
    Dispense one reagent source to a list of wells.

    Tip strategy:
    - transfer_mode="accurate": change tip after `tip_change_interval` target-well dispenses.
      This reduces carryover but costs more time because each new tip is pre-wet.
    - transfer_mode="fast": use one tip for the whole reagent/solvent source.
      This saves time by avoiding repeated tip changes and pre-wetting steps.

    In both modes, a fresh tip is used when a new reagent/solvent source is started.

    Returns a list of dispense records. For kinetic analysis, the `finish_timestamp`
    of the dialdehyde dispense is the practical reaction-start timestamp for each well.
    """
    if total_volume <= 0:
        raise ValueError(f"{reagent_name} volume must be positive.")

    if len(target_well_names) == 0:
        raise ValueError(f"No target wells supplied for {reagent_name}.")

    if tip_change_interval <= 0:
        raise ValueError("tip_change_interval must be positive.")

    if transfer_mode not in {"accurate", "fast"}:
        raise ValueError('transfer_mode must be either "accurate" or "fast".')

    if transfer_mode == "fast":
        effective_tip_change_interval = len(target_well_names)
        log_step(
            protocol,
            f"Fast mode enabled for {reagent_name}: using one tip for this reagent/solvent source."
        )
    else:
        effective_tip_change_interval = tip_change_interval
        log_step(
            protocol,
            f"Accurate mode enabled for {reagent_name}: changing tip after every "
            f"{effective_tip_change_interval} target-well dispense(s)."
        )

    has_tip = False
    dispenses_since_tip_change = 0
    tip_number_for_reagent = 0
    dispense_records = []

    try:
        for dispense_order_index, well_name in enumerate(target_well_names, start=1):
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
                )

            start_timestamp = None
            finish_timestamp = None
            relative_finish_seconds = None

            if log_each_dispense_time and log_absolute_time:
                start_timestamp = log_absolute_time_tick(
                    protocol,
                    f"Starting dispense: {total_volume} uL {reagent_name} to {well_name}."
                )
            elif log_each_dispense_time and timer_start is not None:
                log_time_tick(protocol, timer_start, f"Starting dispense: {total_volume} uL {reagent_name} to {well_name}.")
            else:
                log_step(protocol, f"Adding {total_volume} uL {reagent_name} to {well_name}.")

            transfer_with_split_dispenses(
                pipette=pipette,
                source_well=source_well,
                target_well=plate[well_name],
                total_volume=total_volume,
                dispense_rate=dispense_rate,
                max_dispense=max_dispense,
                air_gap_volume=air_gap_volume,
            )

            if log_each_dispense_time and log_absolute_time:
                finish_timestamp = log_absolute_time_tick(
                    protocol,
                    f"Finished dispense: {total_volume} uL {reagent_name} to {well_name}."
                )
            elif log_each_dispense_time and timer_start is not None:
                relative_finish_seconds = log_time_tick(
                    protocol,
                    timer_start,
                    f"Finished dispense: {total_volume} uL {reagent_name} to {well_name}."
                )

            dispense_records.append({
                "well": well_name,
                "dispense_order_index": dispense_order_index,
                "reagent_name": reagent_name,
                "total_volume_uL": total_volume,
                "start_timestamp": start_timestamp,
                "finish_timestamp": finish_timestamp,
                "relative_finish_seconds": relative_finish_seconds,
            })

            dispenses_since_tip_change += 1

            if dispenses_since_tip_change == effective_tip_change_interval:
                pipette.drop_tip()
                has_tip = False
                log_step(protocol,
                    f"Dropped tip {tip_number_for_reagent} after {effective_tip_change_interval} target-well "
                    f"dispenses for {reagent_name}."
                )
    finally:
        if has_tip:
            pipette.drop_tip()
            log_step(
                protocol,
                f"Dropped final tip {tip_number_for_reagent} for {reagent_name} after "
                f"{dispenses_since_tip_change} target-well dispense(s)."
            )

    return dispense_records


def build_reverse_timepoint_well_order(timepoint_map: list[dict[str, Any]]) -> list[str]:
    """
    Return wells ordered from longest nominal kinetic time point to shortest.

    This is useful because the reaction starts when the second reagent is added.
    Starting the longest time point first and the shortest time point last makes
    later manual sampling/quenching easier.
    """
    ordered_wells = []
    for entry in sorted(timepoint_map, key=lambda item: item["time_min"], reverse=True):
        ordered_wells.extend(entry["wells"])
    return ordered_wells


# Summarise kinetic start times using the middle replicate of each triplicate.
def summarise_middle_replicate_finish_times(
    dispense_records: list[dict[str, Any]],
    timepoint_map: list[dict[str, Any]],
    protocol: Optional[Any] = None,
    echo: bool = True,
    sort_by_nominal_time: bool = True,
) -> list[dict[str, Any]]:
    """
    Summarise kinetic start times using the middle replicate of each triplicate.

    This function is safe to use when dialdehyde was added in reverse time-point order.
    It does not assume that the dispense records are in the same order as `timepoint_map`.
    Instead, it matches records by well name.

    For each time point, the middle well in `entry["wells"]` is used as the representative
    timestamp. For triplicates such as A1/B1/C1, this uses B1; for D1/E1/F1, this uses E1.

    Absolute timestamps are recorded to the nearest second.
    """
    records_by_well = {record["well"]: record for record in dispense_records}
    summary = []

    for entry in timepoint_map:
        wells = entry["wells"]
        middle_well = wells[len(wells) // 2]
        middle_record = records_by_well.get(middle_well)

        if middle_record is None:
            raise ValueError(f"No dispense record found for middle replicate well {middle_well}.")

        finish_timestamp = middle_record.get("finish_timestamp")
        relative_finish_seconds = middle_record.get("relative_finish_seconds")
        dispense_order_index = middle_record.get("dispense_order_index")

        if finish_timestamp is None and relative_finish_seconds is None:
            raise ValueError(
                f"No timestamp found for middle replicate well {middle_well}. "
                "Run dispense_to_wells_with_tip_changes() with log_each_dispense_time=True "
                "and either log_absolute_time=True or timer_start provided."
            )

        summary_record = {
            "time_min": entry["time_min"],
            "timepoint_index": entry["timepoint_index"],
            "plate_region": entry["plate_region"],
            "representative_well": middle_well,
            "dispense_order_index": dispense_order_index,
            "finish_timestamp": finish_timestamp,
            "relative_finish_seconds": relative_finish_seconds,
        }
        summary.append(summary_record)

        if finish_timestamp is not None:
            time_text = f"finished dialdehyde dispense at {finish_timestamp}"
        else:
            time_text = f"finished dialdehyde dispense at t = {relative_finish_seconds:.2f} s"

        log_step(
            protocol,
            f"Representative kinetic start for nominal {entry['time_min']} min "
            f"time point {entry['timepoint_index']} = {middle_well} "
            f"(dispense order {dispense_order_index}), {time_text}.",
            echo=echo,
        )

    if sort_by_nominal_time:
        summary = sorted(summary, key=lambda record: record["time_min"])

    return summary


def log_reverse_dialdehyde_order(
    timepoint_map: list[dict[str, Any]],
    protocol: Optional[Any] = None,
    echo: bool = True,
) -> list[str]:
    """
    Log and return the dialdehyde addition order for reverse-time kinetic setup.

    The returned well order starts the longest nominal time point first and the shortest
    nominal time point last. Within each triplicate, the listed replicate order is preserved,
    e.g. A6, B6, C6 or D2, E2, F2.
    """
    ordered_wells = []
    for entry in sorted(timepoint_map, key=lambda item: item["time_min"], reverse=True):
        wells = entry["wells"]
        ordered_wells.extend(wells)
        log_step(
            protocol,
            f"Reverse dialdehyde order: nominal {entry['time_min']} min time point, "
            f"timepoint {entry['timepoint_index']} ({entry['plate_region']}) -> {', '.join(wells)}.",
            echo=echo,
        )

    return ordered_wells
