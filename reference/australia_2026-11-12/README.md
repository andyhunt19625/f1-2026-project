# Australia (career), 12 Nov 2026: reference

- Qualifying session folder on the PC: `D:\RealF1-esports\sessions\20261112T215350Z_64cfe759329ddc74` (sessionType 8).
- Pole lap: lap 3 (from the line), 1:16.749. ERS mode none 70%, overtake 30% (manual deploy).
- Lap 2 (from the line), 1:17.707: medium 82%, overtake 18%. Lap 1: 1:17.896, hotlap 71%.
- `pole_map.json`: output of `python -m race_engineer.track_map <file> --lap "lap 3 (from the line)" --ref "lap 2 (from the line)"`.
  Sections add up to 0.986 s against the real gap of 0.958 s.
- `pole_lap_map.html`: the map page (also published at https://claude.ai/artifact/AhKjxweXiVFrgpzf8sDqda).
- Main finding: 0.711 s of the gain was 3600-4300 m, where the pole lap still had battery
  (lap 2 battery empty from 2630 m, pole lap from 3931 m).
- Setup used: front wing 25, rear wing 18, diff on 80 / off 30, brake bias 54, front tyres 24.1.
