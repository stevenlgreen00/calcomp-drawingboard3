# Captures

Recordings from the real tablet (model 34180, cordless pen and 16-button
puck). Only the ones the tests and tools use are published.

| File | What it is | Used by |
|---|---|---|
| `20261016-200836.json` / `.txt` | `tools/capture.py` session: puck hover, movement and all 16 buttons (raw bytes and decoded packets; `.txt` is the summary) | `driver/test` (`make test`) |
| `20261016-201619.json` / `.txt` | capture.py: puck buttons again, and pen ↔ puck switching | `driver/test` |
| `20261016-204600.json` / `.txt` | capture.py: pen hover, corners, tip and barrel buttons | `driver/test` |
| `evtest-sweep.log` | `evtest` output while the puck was dragged top to bottom at the left, middle and right. It shows both faulty strips | `tools/scan_bands.py --replay captures/evtest-sweep.log --axis y` |
| `gaps-20261017-024933.json` | `tools/gaps.py`: pauses in the packet stream through a PL2303 adapter | reference for `prox_timeout_ms` |

`driver/test/make_vectors.py` turns the three capture.py sessions into about
9800 test vectors, and `make -C driver test` checks the kernel driver's decoder
against them.
