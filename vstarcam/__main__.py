"""Arranque de la aplicación: python3 -m vstarcam"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .alerts import Alerter
from .cloud import CloudUploader
from .config import Config
from .core import CameraManager
from .db import DB
from .go2rtc import Go2RTC
from .server import App, build_server
from .util import ensure_dirs, local_ip, logger, setup_logging, which

BANNER = r"""
 __     __ ___ _______  ___   ___   ___  ___  ___
 \ \   / // __|_   _| /_\ | _ \ / __|/ _ \|   \
  \ \ / / \__ \ | |  / _ \|   /| (__| (_) | |) |
   \_V_/  |___/ |_| /_/ \_\_|_\ \___|\___/|___/
   Visor y control local de cámaras VStarcam PTZ
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vstarcam", description="Visor y control PTZ local")
    parser.add_argument("--host", help="Interfaz de escucha (por defecto la de config.json)")
    parser.add_argument("--port", type=int, help="Puerto HTTP (por defecto 8080)")
    parser.add_argument("--no-go2rtc", action="store_true", help="No arrancar go2rtc")
    parser.add_argument("--no-browser", action="store_true", help="No abrir el navegador")
    parser.add_argument("--debug", action="store_true", help="Log detallado")
    args = parser.parse_args(argv)

    setup_logging(logging.DEBUG if args.debug else logging.INFO)
    ensure_dirs()

    config = Config()
    if args.host:
        config.data["server"]["host"] = args.host
    if args.port:
        config.data["server"]["port"] = args.port
    if args.no_go2rtc:
        config.data["go2rtc"]["enabled"] = False

    if not which("ffmpeg"):
        logger.warning(
            "ffmpeg no está instalado. No habrá grabación ni capturas. Ejecuta ./setup.sh"
        )

    db = DB()
    go2rtc = Go2RTC(config)
    cloud = CloudUploader(config, db)
    alerter = Alerter(config)
    manager = CameraManager(config, db, go2rtc, cloud, alerter)

    if config.get("go2rtc", "enabled", default=True):
        if not go2rtc.start():
            logger.warning("Continuando sin go2rtc: la vista será por instantáneas.")
    go2rtc.start_watchdog()

    manager.start()
    cloud.start()

    host = config.get("server", "host", default="0.0.0.0")
    port = int(config.get("server", "port", default=8080))
    try:
        httpd = build_server(host, port, App(config, manager))
    except OSError as exc:
        logger.error("No se pudo abrir el puerto %s:%s → %s", host, port, exc)
        return 1

    ip = local_ip()
    print(BANNER)
    print(f"  Panel local:  http://127.0.0.1:{port}")
    print(f"  En tu red:    http://{ip}:{port}")
    if config.get("server", "password", default=""):
        print("  Protegido con contraseña ✔")
    print("  Pulsa Ctrl+C para salir\n")

    stop_event = threading.Event()

    def _shutdown(signum=None, frame=None):  # noqa: ANN001, ARG001
        if stop_event.is_set():
            return
        stop_event.set()
        logger.info("Cerrando…")
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if not args.no_browser:
        try:
            import webbrowser

            threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        except Exception:  # noqa: BLE001
            pass

    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        manager.stop()
        cloud.stop()
        go2rtc.shutdown()
        db.close()
        httpd.server_close()
        logger.info("Hasta luego")
    return 0


if __name__ == "__main__":
    sys.exit(main())
