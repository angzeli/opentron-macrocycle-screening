# 🧪 Emerging Tech Lab - Opentrons Macrocycle Screening

## 🤖 Automated OT-2 Macrocycle Synthesis Protocols

This repository contains Jupyter notebooks and helper functions for automated macrocycle synthesis screening on an Opentrons OT-2 liquid handling platform.

The protocols are written for Opentrons API v2.19 and a P300 single-channel Gen2 pipette. They are designed for chemistry screening workflows using a 48-well reaction plate and an 8-well source plate.

## 📦 Files

```text
opentrons_macrocycle_screening/
├── calibration/
│   ├── calibration_curve.ipynb
│   ├── accuracy_test_chloroform_before_optimisation.pdf
│   └── accuracy_test_chloroform_after_optimisation.pdf
├── conc_opentrons.ipynb
├── conc_opentrons_helpers.py
├── kinetic_opentrons.ipynb
├── kinetic_opentrons_20260515.ipynb
├── kinetic_opentrons_helpers.py
├── solvent_screen_opentrons.ipynb
├── solvent_screen_opentrons_helpers.py
├── LICENSE
├── pyproject.toml
├── README.md
└── .gitignore
```

## 🧭 What You Can Do With These Files

- Run kinetic macrocycle screening with vertical triplicates and up to 16 time points on one 48-well plate.
- Run solvent screening across multiple solvent conditions, including conditions prepared from different 8-well source plates.
- Run concentration screening by editing per-well diamine, dialdehyde, and chloroform top-up volumes.
- Use the helper files for plate-map validation, split dispensing, pre-wetting, tip handling, timing records, and notebook-visible logging.
- Use the calibration notebook and saved PDFs to review chloroform dispense accuracy before running chemistry.

## 🔧 Requirements

- Opentrons OT-2
- Opentrons API v2.19-compatible environment
- P300 single-channel Gen2 pipette
- `greenaway_48_wellplate_3750ul` custom labware definition
- `greenaway_8_wellplate_20000ul` custom labware definition
- Python 3.9 or newer for the helper files

If running the notebooks directly from Jupyter, the active Python environment must have the Opentrons Python package available. If running through the Opentrons App workflow, make sure the robot/app has the required custom labware definitions loaded.

## 📝 Protocol Notes

- Diamine solution is added before dialdehyde solution.
- Dialdehyde is dispensed using the slow dispense rate for dropwise addition.
- Volumes larger than the configured single-dispense limit are split into multiple dispenses.
- P300 capacity is checked including air gaps.
- Target wells are validated against the 48-well plate layout.
- Duplicate target wells are detected before the run.
- Notebook-visible logging is printed and also sent to the Opentrons protocol log.

## 📋 Recommended Run Checklist

1. Confirm the OT-2 deck layout matches the notebook slots.
2. Confirm the P300 single-channel Gen2 pipette is mounted on the configured side.
3. Confirm custom labware definitions are installed and calibrated.
4. Review `CONDITIONS_TO_RUN`, source wells, time points, and dispense volumes.
5. Run a dry simulation or dry run before adding chemistry.
6. Confirm there are enough P300 tips for the selected transfer mode.

## 👤 Author

Angze Li
