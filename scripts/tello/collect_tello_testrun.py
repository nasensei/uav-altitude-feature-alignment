"""Convenience entry point for the guarded 30 m Tello collection plan."""

from collect_tello_altitude import main


if __name__ == "__main__":
    raise SystemExit(main(default_target_altitude_m=1.0))
