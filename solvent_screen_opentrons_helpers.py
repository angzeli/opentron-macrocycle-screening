# -----------------------------
# Solvent-screen Opentrons helper functions
# -----------------------------

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Optional

# Default liquid-handling parameters. These can be overridden from the notebook.
DEFAULT_MAX_DISPENSE = 200          # uL; two thirds of the P300 volume
DEFAULT_PRE_WET_CYCLES = 3
DEFAULT_PRE_WET_VOLUME = 180        # uL
DEFAULT_AIR_GAP_VOLUME = 15         # uL
DEFAULT_TIP_CHANGE_INTERVAL = 3     # used in accurate mode
DEFAULT_TRANSFER_MODE = "fast"      # "fast" = one tip per reagent/solvent source; "accurate" = change tips regularly

# Optional robot movement-speed settings.
# These control gantry/plunger movement speed rather than liquid flow rate.
DEFAULT_PIPETTE_DEFAULT_SPEED = 400  # mm/s
DEFAULT_MAX_HEAD_SPEED = 400         # mm/s for X/Y/Z/A axes when supported
DEFAULT_TOUCH_TIP_SPEED = 40         # mm/s; keep rim-touching slow to avoid splashing/droplet flicking

P300_FALLBACK_MAX_VOLUME = 300


def _pipette_max_volume(pipette: Any) -> float:
    """Return the pipette capacity, falling back to P300 capacity for simulation-like objects."""
    try:
        return float(getattr(pipette, "max_volume", P300_FALLBACK_MAX_VOLUME))
    except (TypeError, ValueError):
        return float(P300_FALLBACK_MAX_VOLUME)


def log_step(protocol: Optional[Any], message: str, echo: bool = True) -> None:
    """Print a message in the notebook and also write it to the Opentrons protocol log."""
    if echo:
        print(message)
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


def load_source_plates(
    protocol: Any,
    labware_name: str,
    plate_locations: Iterable[Any],
    name_prefix: str = "plate_8",
    echo: bool = True,
) -> dict[str, Any]:
    """
    Load one or more source plates and return them in a named dictionary.

    Example:
    source_plates = load_source_plates(protocol, "greenaway_8_wellplate_20000ul", [3, 4])
    plate_8_1 = source_plates["plate_8_1"]
    plate_8_2 = source_plates["plate_8_2"]
    """
    locations = list(plate_locations)
    if len(locations) == 0:
        raise ValueError("At least one source plate location is required.")

    normalised_locations = [str(location).strip() for location in locations]
    if any(location == "" for location in normalised_locations):
        raise ValueError("Source-plate deck slots must be non-empty.")

    if len(set(normalised_locations)) != len(normalised_locations):
        raise ValueError("Duplicate source-plate deck slots detected.")

    if not isinstance(name_prefix, str) or name_prefix.strip() == "":
        raise ValueError("name_prefix must be a non-empty string.")

    source_plates = {}
    for index, location in enumerate(locations, start=1):
        plate_name = f"{name_prefix.strip()}_{index}"
        source_plates[plate_name] = protocol.load_labware(
            labware_name,
            location=location,
        )
        log_step(protocol, f"Loaded source plate {plate_name} in deck slot {location}.", echo=echo)

    return source_plates


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
    - pipette.default_speed control robot movement speed.
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


def build_vertical_triplicate_wells(condition_index: int) -> list[str]:
    """
    Build vertical triplicate wells for one solvent condition.

    Conditions 1-8 use rows A/B/C across columns 1-8.
    Conditions 9-16 use rows D/E/F across columns 1-8.
    """
    if not isinstance(condition_index, int) or isinstance(condition_index, bool):
        raise ValueError("condition_index must be an integer between 1 and 16.")

    if not 1 <= condition_index <= 16:
        raise ValueError("condition_index must be between 1 and 16 inclusive.")

    if condition_index <= 8:
        rows = ["A", "B", "C"]
        column = condition_index
    else:
        rows = ["D", "E", "F"]
        column = condition_index - 8

    return [f"{row}{column}" for row in rows]


def build_solvent_screen_plate_map(
    solvent_conditions: dict[str, dict[str, Any]],
    conditions_to_run: list[str],
    protocol: Optional[Any] = None,
) -> dict[str, dict[str, Any]]:
    """
    Build and validate the target-well map for a solvent screen.

    Each solvent condition uses one vertical triplicate block:
    - target_index 1 -> A1, B1, C1
    - target_index 8 -> A8, B8, C8
    - target_index 9 -> D1, E1, F1
    - target_index 16 -> D8, E8, F8

    If a condition defines "target_index", that block is used. Otherwise,
    the 1-based order in `conditions_to_run` is used.
    """
    if len(conditions_to_run) == 0:
        raise ValueError("Select at least one solvent condition to run.")

    if len(set(conditions_to_run)) != len(conditions_to_run):
        raise ValueError("Duplicate solvent condition names detected in conditions_to_run.")

    if len(conditions_to_run) > 16:
        raise ValueError("This solvent-screen layout supports at most 16 conditions per 48-well plate.")

    used_wells = []
    used_target_indices = []
    plate_map = {}

    for run_order_index, condition_name in enumerate(conditions_to_run, start=1):
        if condition_name not in solvent_conditions:
            raise ValueError(f"Unknown solvent condition: {condition_name}")

        condition = solvent_conditions[condition_name]
        for required_key in ["diamine_source", "dialdehyde_source"]:
            if required_key not in condition:
                raise ValueError(f'Solvent condition "{condition_name}" must define "{required_key}".')
            if condition[required_key] is None:
                raise ValueError(f'Solvent condition "{condition_name}" has no value for "{required_key}".')

        target_index = condition.get("target_index", run_order_index)
        target_wells = build_vertical_triplicate_wells(target_index)

        if target_index in used_target_indices:
            raise ValueError(f"Duplicate target_index detected: {target_index}.")

        plate_map[condition_name] = {
            **condition,
            "target_index": target_index,
            "target_wells": target_wells,
        }
        used_target_indices.append(target_index)
        used_wells.extend(target_wells)

        log_step(protocol, f"Condition: {condition_name} -> target index {target_index}: {', '.join(target_wells)}")

    if len(set(used_wells)) != len(used_wells):
        raise ValueError("Duplicate target wells detected. Check solvent-condition target indices.")

    return plate_map


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
        log_step(
            protocol,
            f"Warning: {name} volume is {volume} uL and will be split into multiple dispenses of <= {max_single_dispense} uL.",
        )


def pre_wet_source(
    pipette: Any,
    protocol: Any,
    source_well: Any,
    cycles: int = DEFAULT_PRE_WET_CYCLES,
    volume: float = DEFAULT_PRE_WET_VOLUME,
    delay_seconds: float = 5,
    touch_tip_speed: float = DEFAULT_TOUCH_TIP_SPEED,
) -> None:
    """Pre-wet the current pipette tip using the correct reagent source, with slow rim-touching."""
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

    for amount in calculate_dispense(total_vol=total_volume, max_dispense=max_dispense, equal_dispenses=True):
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
    log_each_dispense_time: bool = False,
    log_absolute_time: bool = False,
) -> list[dict[str, Any]]:
    """
    Dispense one reagent source to a list of wells.

    transfer_mode="fast" uses one tip for the whole reagent/solvent source.
    transfer_mode="accurate" changes tip after `tip_change_interval` wells.
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
        log_step(protocol, f"Fast mode enabled for {reagent_name}: using one tip for this reagent/solvent source.")
    else:
        effective_tip_change_interval = tip_change_interval
        log_step(protocol, f"Accurate mode enabled for {reagent_name}: changing tip after every {effective_tip_change_interval} target-well dispense(s).")

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
            if log_each_dispense_time and log_absolute_time:
                start_timestamp = log_absolute_time_tick(protocol, f"Starting dispense: {total_volume} uL {reagent_name} to {well_name}.")
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
                finish_timestamp = log_absolute_time_tick(protocol, f"Finished dispense: {total_volume} uL {reagent_name} to {well_name}.")

            dispense_records.append({
                "well": well_name,
                "dispense_order_index": dispense_order_index,
                "reagent_name": reagent_name,
                "total_volume_uL": total_volume,
                "start_timestamp": start_timestamp,
                "finish_timestamp": finish_timestamp,
            })

            dispenses_since_tip_change += 1
            if dispenses_since_tip_change == effective_tip_change_interval:
                pipette.drop_tip()
                has_tip = False
                log_step(protocol, f"Dropped tip {tip_number_for_reagent} after {effective_tip_change_interval} target-well dispense(s) for {reagent_name}.")
    finally:
        if has_tip:
            pipette.drop_tip()
            log_step(protocol, f"Dropped final tip {tip_number_for_reagent} for {reagent_name} after {dispenses_since_tip_change} target-well dispense(s).")

    return dispense_records


def summarise_solvent_screen_records(
    dispense_records_by_condition: dict[str, list[dict[str, Any]]],
    protocol: Optional[Any] = None,
    echo: bool = True,
) -> list[dict[str, Any]]:
    """Summarise dispense records by solvent condition for notebook inspection."""
    summary = []
    for condition_name, records in dispense_records_by_condition.items():
        wells = [record["well"] for record in records]
        summary_record = {
            "condition": condition_name,
            "wells": wells,
            "n_wells": len(wells),
        }
        summary.append(summary_record)
        log_step(protocol, f"Solvent-screen condition {condition_name}: {', '.join(wells)}", echo=echo)
    return summary
