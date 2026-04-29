# Hardware-in-the-loop tests

These tests poll real industrial devices over the network. They are
**skipped by default** because most environments don't have the hardware
attached. To run them, set the relevant environment variable to the
device's IP and run pytest with the `hardware` marker:

```bash
export HW_CM2000_IP=10.4.12.55     # Schneider CM2000 power meter
export HW_MTZ_IP=10.4.12.61        # Schneider Masterpact MTZ via IFE
export HW_SEL751_IP=10.4.12.74     # SEL-751 protective relay
export HW_INSULGARD_IP=10.4.12.81  # Eaton InsulGard PD monitor
export HW_OPT100_IP=10.4.12.92     # Vaisala OPT100 DGA
export HW_QUALITROL_IP=10.4.12.95  # Qualitrol 118ITM
export HW_NVML=1                   # NVML/DCGM on local node

pytest tests/hardware/ -m hardware -v
```

## What these tests assert

Each test polls the device's published register map (per spec §3.x), checks
the values are within physically plausible ranges, and verifies the worker
+ attestation pipeline persists results correctly.

These are **acceptance tests** — they prove our code works against a real
device. If a test fails it could mean:

- The device's firmware exposes a different register map than the spec.
  → Review the device's manual, compare to `config_context.registers`,
  update the Config Context, re-run.
- The device requires a non-default function code or unit_id.
- Byte/word ordering differs from the spec defaults.

For a new device type, copy `test_cm2000.py` as a template and update the
register map / acceptance ranges.

## Safety

Read tests are non-destructive. **Active write tests** (battery transfer,
breaker trip) are explicitly omitted — they belong in a controlled
commissioning run with `manual_confirmation` gates from Module 10.
