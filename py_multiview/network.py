"""Network helpers."""

import socket


def local_ip() -> str:
    """LAN address of the interface holding the default route ("" if offline).

    No packet is sent: connecting a UDP socket only selects the route.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return ""
