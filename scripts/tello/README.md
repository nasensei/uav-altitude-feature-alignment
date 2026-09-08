# Tello data collection

These scripts collect 720p frames from the Tello SDK video stream and save synchronized flight telemetry on the controlling computer.

## Hardware limits

The standard Ryze Tello has a published maximum flight height of 30 m. Its Vision Positioning System is only effective from 0.3 to 10 m and works best from 0.3 to 6 m. The collector therefore rejects any target above 30 m and prints a notice for targets above 10 m.

There is no 80 m Tello script. Collecting real 80 m imagery requires a different aircraft rated for that altitude and an implementation for that aircraft's SDK.

The Tello SDK exposes a 720p video stream but no still-photo capture command. The program saves selected stream frames as high-quality JPEG files. These are not the Tello app's 5 MP still photographs.

## Research-use limitation

The Tello imaging camera is fixed rather than mounted on a controllable nadir gimbal. If the main experiment assumes a downward-facing camera and linear GSD change with altitude, use Tello collection as a pipeline pilot unless camera direction, target position, and slant range can be held consistent. A gimbal-equipped aircraft is a better choice for the final controlled 30/80 m study.

Official references:

- Tello specifications: https://www.ryzerobotics.com/tello/specs
- Tello SDK 2.0 guide: https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf
- Tello user manual: https://dl-cdn.ryzerobotics.com/downloads/Tello/20180404/Tello_User_Manual_V1.2_EN.pdf

## Installation

From the repository root, activate the Python environment and install the additional flight dependencies:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-tello.txt
```

Connect the controlling computer to the Tello's `TELLO-XXXXXX` Wi-Fi network before executing a flight.

## Prepare with a dry run

Every invocation is a dry run unless `--execute` is included:

```powershell
python scripts/tello/collect_tello_altitude.py --target-altitude-m 2
```

Start with controlled flights at 2 m, 5 m, and 10 m before considering the device ceiling. The 30 m plan can be inspected with:

```powershell
python scripts/tello/collect_tello_30m.py
```

## Execute a collection

The following example takes off, ascends in 2 m steps, waits five seconds, saves 20 frames one second apart, and lands:

```powershell
python scripts/tello/collect_tello_altitude.py `
  --target-altitude-m 5 `
  --capture-count 20 `
  --capture-interval-seconds 1 `
  --session-id tello_site01_5m_run01 `
  --site-id site01 `
  --execute
```

The program displays the complete plan and requires an exact typed confirmation before sending a takeoff command. Press `Ctrl+C` to stop collection and request landing.

At the 30 m device ceiling:

```powershell
python scripts/tello/collect_tello_30m.py `
  --session-id tello_site01_30m_run01 `
  --site-id site01 `
  --acknowledge-vps-limit `
  --execute
```

Only execute a flight after checking the site, weather, aircraft condition, battery, local operating rules, visual line of sight, and a clear landing area. Above the effective vision-positioning range, the aircraft may enter Attitude mode and drift horizontally.

## Output

Each run creates a separate session under:

```text
data/raw/tello/<session_id>/
|-- images/
|   |-- <session_id>_0000.jpg
|   `-- ...
|-- capture_manifest.csv
`-- session.json
```

`capture_manifest.csv` records the requested altitude, reported height, relative barometer reading, time-of-flight reading, battery, attitude, temperature, image dimensions, timestamp, site, and image path. Height telemetry should be treated as a sensor measurement rather than exact surveyed altitude, especially above the Vision Positioning System's effective range.

The raw session remains excluded from Git. After checking the images and assigning physical object identities, add selected observations to `data/manifest.csv` for the Q3a analysis.
