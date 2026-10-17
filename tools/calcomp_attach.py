#!/usr/bin/env python3
"""Attach a serial port to the kernel serio layer as a CalComp tablet.

This does what inputattach does for its built-in device types: set the port to
9600 8N1, switch it to the serport line discipline (N_MOUSE), and tag the new
serio port as SERIO_RS232 / protocol SERIO_CALCOMP (0xc0) so that calcomp_drv
binds to it. inputattach itself has no CalComp type, hence this helper.

The port stays attached while this program runs. Stop it (Ctrl-C or SIGTERM)
to detach; the driver then removes the input device.

Needs root (serport requires CAP_SYS_ADMIN) and the serport module.

    sudo python3 tools/calcomp_attach.py /dev/ttyUSB0
"""

import argparse
import fcntl
import os
import signal
import struct
import sys
import termios

N_TTY = 0
N_MOUSE = 2                 # serport line discipline
SERIO_CALCOMP = 0xc0        # must match calcomp_drv.c


def _iow(type_char, nr, size):
    """Linux _IOW() for x86/arm (dir bits 01 at bit 30)."""
    return (1 << 30) | (size << 16) | (ord(type_char) << 8) | nr


SPIOCSTYPE = _iow("q", 0x01, struct.calcsize("L"))   # _IOW('q', 0x01, unsigned long)
BAUDS = {9600: termios.B9600, 19200: termios.B19200, 4800: termios.B4800,
         2400: termios.B2400, 1200: termios.B1200}


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def setup_tty(fd, baud):
    attrs = termios.tcgetattr(fd)
    attrs[0] = termios.IGNBRK | termios.IGNPAR                       # iflag
    attrs[1] = 0                                                      # oflag
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL | termios.HUPCL   # 8N1, no flow control
    attrs[3] = 0                                                      # lflag
    attrs[4] = attrs[5] = BAUDS[baud]
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("port", nargs="?", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600, choices=sorted(BAUDS))
    args = ap.parse_args()

    try:
        fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY)
    except OSError as e:
        sys.exit(f"can't open {args.port}: {e}")

    # Turn SIGTERM into a normal exit so the line discipline gets restored.
    signal.signal(signal.SIGTERM, _raise_interrupt)

    ldisc_set = False   # only undo the line discipline if we set it
    attached = False
    try:
        try:
            setup_tty(fd, args.baud)
        except termios.error as e:
            # serport rejects normal tty ioctls, so this means it's attached already
            sys.exit(f"can't set up {args.port}: {e.args[-1]}\n"
                     "It is probably still attached by another calcomp_attach. Stop that one "
                     "(Ctrl-C in its terminal, or: sudo pkill -f calcomp_attach) and try again.")
        try:
            fcntl.ioctl(fd, termios.TIOCSETD, struct.pack("i", N_MOUSE))
            ldisc_set = True
        except OSError as e:
            sys.exit(f"can't switch {args.port} to the serport line discipline: {e}\n"
                     "(run as root, and load serport: sudo modprobe serport)")
        try:
            fcntl.ioctl(fd, SPIOCSTYPE, struct.pack("L", SERIO_CALCOMP))
        except OSError as e:
            sys.exit(f"SPIOCSTYPE failed on {args.port}: {e}")
        attached = True

        print(f"{args.port} attached as a CalComp serio port; the driver is setting up the tablet. "
              "Ctrl-C to detach.", flush=True)
        # serport registers the serio port when the line is read and keeps it
        # until the read returns (hangup or signal).
        try:
            os.read(fd, 1)
        except KeyboardInterrupt:
            pass
        except OSError:
            pass
    except KeyboardInterrupt:
        pass
    finally:
        if ldisc_set:
            try:
                fcntl.ioctl(fd, termios.TIOCSETD, struct.pack("i", N_TTY))
            except OSError:
                pass
        os.close(fd)
        if attached:
            print(f"{args.port} detached.")


if __name__ == "__main__":
    main()
