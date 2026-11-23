"""
Main.py

Thin entry point for the Formula Legends race engineer tool. This file
launches the GUI (gui/app.py), which in turn drives the actual capture and
telemetry logic (telemetry/telemetry_engine.py) from a background thread.

Run this file to start the application:

    python Main.py

The GUI's "Ready to Race" button starts/stops data gathering -- nothing
runs before you click it.
"""

from gui.app import main

if __name__ == "__main__":
    main()
