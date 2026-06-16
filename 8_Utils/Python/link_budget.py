#!/usr/bin/env python3
"""AERIS-10 radar link budget / sensitivity calculator.

Estimates the minimum detectable radar cross section (RCS) versus range and,
conversely, the maximum detection range versus target RCS, using the coherent
radar range equation with pulse-compression and Doppler-integration gains.

All default parameters are sourced from this repository (see references in the
PARAMS table below). The headline result this reproduces:

    * Min detectable RCS at 15 km  ~ 0.3 m^2  (small drone / person-sized)
    * Max range for a 1 m^2 target ~ 20 km    (matches the README "20 km" spec)

This is the AERIS-10X (Extended, 20 km) configuration. The Nexus (3 km) variant
uses 1 W/channel and the 128-element patch array, so 15 km only applies to the
Extended build.

Run:
    python3 link_budget.py            # print the default budget + tables
    python3 link_budget.py --help     # list overridable parameters
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass

C_LIGHT = 2.998e8


@dataclass
class RadarParams:
    """AERIS-10X (Extended) link-budget parameters.

    Repo references:
      f0, chirp_bw, if      -> 9_Firmware/9_2_FPGA/tb/cosim/radar_scene.py
      tau (30 us long chirp) -> 9_Firmware/9_2_FPGA/plfm_chirp_controller.v
      n_coh (chirps/subframe)-> radar_scene.py (32-pt Doppler FFT, 16 long PRIs)
      tx power (16 x 10 W)   -> README.md (QPA2962 GaN, 10 W x 16)
      array geometry / gain  -> 5_Simulations/array_pattern_Kaiser25dB_like.py
    Noise figure and system losses are NOT documented in the repo and are
    engineering estimates -- they are the weakest assumptions here.
    """

    f0: float = 10.5e9          # carrier frequency (Hz)
    tx_power_w: float = 160.0   # total transmit power: 16 channels x 10 W
    gain_dbi: float = 32.0      # full 512-element array gain (Tx = Rx), aperture-derived
    noise_figure_db: float = 4.0  # system noise figure (ESTIMATE)
    losses_db: float = 4.0      # RF + beam-shape + processing losses (ESTIMATE)
    chirp_bw: float = 20e6      # chirp sweep bandwidth (Hz) -> 7.5 m range resolution
    pulse_width: float = 30e-6  # long chirp duration (s)
    n_coh: int = 16             # coherent chirps integrated per subframe
    snr_req_db: float = 13.0    # required SNR for Pd~0.9, Pfa~1e-6 with CFAR
    temperature_k: float = 290.0

    @property
    def wavelength(self) -> float:
        return C_LIGHT / self.f0

    @property
    def tx_power_dbm(self) -> float:
        return 10 * math.log10(self.tx_power_w) + 30

    @property
    def pulse_compression_db(self) -> float:
        """Time-bandwidth product gain."""
        return 10 * math.log10(self.pulse_width * self.chirp_bw)

    @property
    def doppler_gain_db(self) -> float:
        """Coherent integration gain across chirps."""
        return 10 * math.log10(self.n_coh)

    @property
    def processing_gain_db(self) -> float:
        return self.pulse_compression_db + self.doppler_gain_db

    @property
    def noise_power_dbm(self) -> float:
        """N = kTB * F, with kT0 = -174 dBm/Hz."""
        return -174.0 + 10 * math.log10(self.chirp_bw) + self.noise_figure_db


_FOUR_PI_CUBED_DB = 10 * math.log10((4 * math.pi) ** 3)


def min_detectable_rcs_dbsm(p: RadarParams, range_m: float) -> float:
    """Minimum detectable RCS (dBsm) at the given range.

    Rearranged coherent radar equation, solving for sigma at SNR = snr_req:
        sigma = SNRreq + (4pi)^3 + 40log10(R) + N + L
                - Pt - Gt - Gr - 20log10(lambda) - Gp
    """
    return (
        p.snr_req_db
        + _FOUR_PI_CUBED_DB
        + 40 * math.log10(range_m)
        + p.noise_power_dbm
        + p.losses_db
        - p.tx_power_dbm
        - 2 * p.gain_dbi
        - 20 * math.log10(p.wavelength)
        - p.processing_gain_db
    )


def max_range_m(p: RadarParams, rcs_dbsm: float) -> float:
    """Maximum detection range (m) for a target of the given RCS."""
    r4_db = (
        p.tx_power_dbm
        + 2 * p.gain_dbi
        + 20 * math.log10(p.wavelength)
        + rcs_dbsm
        + p.processing_gain_db
        - _FOUR_PI_CUBED_DB
        - p.noise_power_dbm
        - p.losses_db
        - p.snr_req_db
    )
    return (10 ** (r4_db / 10)) ** 0.25


def _fmt_rcs(dbsm: float) -> str:
    return f"{dbsm:6.1f} dBsm = {10 ** (dbsm / 10):8.4f} m^2"


def main() -> None:
    p = RadarParams()
    ap = argparse.ArgumentParser(description="AERIS-10X radar link budget")
    ap.add_argument("--gain-dbi", type=float, default=p.gain_dbi,
                    help="full array gain, Tx=Rx (default %(default)s)")
    ap.add_argument("--noise-figure-db", type=float, default=p.noise_figure_db,
                    help="system noise figure (default %(default)s)")
    ap.add_argument("--losses-db", type=float, default=p.losses_db,
                    help="system losses (default %(default)s)")
    ap.add_argument("--snr-req-db", type=float, default=p.snr_req_db,
                    help="required detection SNR (default %(default)s)")
    ap.add_argument("--range-km", type=float, default=15.0,
                    help="range for the RCS estimate (default %(default)s)")
    args = ap.parse_args()

    p = RadarParams(
        gain_dbi=args.gain_dbi,
        noise_figure_db=args.noise_figure_db,
        losses_db=args.losses_db,
        snr_req_db=args.snr_req_db,
    )
    r = args.range_km * 1e3

    print("=" * 60)
    print(" AERIS-10X (Extended) Radar Link Budget")
    print("=" * 60)
    print(f" Carrier            : {p.f0 / 1e9:.1f} GHz  (lambda = {p.wavelength * 1000:.2f} mm)")
    print(f" Tx power           : {p.tx_power_w:.0f} W ({p.tx_power_dbm:.1f} dBm)")
    print(f" Array gain (Tx=Rx) : {p.gain_dbi:.1f} dBi")
    print(f" Chirp BW / tau     : {p.chirp_bw / 1e6:.0f} MHz / {p.pulse_width * 1e6:.0f} us")
    print(f" Range resolution   : {C_LIGHT / (2 * p.chirp_bw):.1f} m")
    tbp = p.pulse_width * p.chirp_bw
    print(f" Pulse compression  : {p.pulse_compression_db:.1f} dB (tau*B = {tbp:.0f})")
    print(f" Doppler integration: {p.doppler_gain_db:.1f} dB ({p.n_coh} chirps)")
    print(f" Processing gain    : {p.processing_gain_db:.1f} dB")
    print(f" Noise figure / loss: {p.noise_figure_db:.1f} dB / {p.losses_db:.1f} dB")
    print(f" Noise power        : {p.noise_power_dbm:.1f} dBm")
    print(f" Required SNR       : {p.snr_req_db:.1f} dB")

    print("\n--- Minimum detectable RCS ---")
    sigma = min_detectable_rcs_dbsm(p, r)
    print(f" @ {args.range_km:>5.1f} km : {_fmt_rcs(sigma)}")
    for rng_km in (5, 10, 15, 20):
        s = min_detectable_rcs_dbsm(p, rng_km * 1e3)
        print(f" @ {rng_km:>5.1f} km : {_fmt_rcs(s)}")

    print("\n--- Max range vs target RCS ---")
    for name, rcs in [
        ("mini-drone / bird  (0.01 m^2)", -20),
        ("small drone        (0.10 m^2)", -10),
        ("person / small UAV (1.0 m^2) ", 0),
        ("car / light a/c    (10 m^2)  ", 10),
    ]:
        print(f" {name}: Rmax = {max_range_m(p, rcs) / 1000:5.1f} km")

    print("\nNote: 1 m^2 -> ~20 km reproduces the README headline range,")
    print("confirming that spec implicitly assumes a ~1 m^2 target.")


if __name__ == "__main__":
    main()
