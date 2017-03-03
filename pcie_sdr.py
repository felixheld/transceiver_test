#!/usr/bin/env python3

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from transceiver.gtp_7series import GTPQuadPLL, GTP

from litex.build.generic_platform import *


_io = [
    ("clk100", 0,
        Subsignal("p", Pins("F6")),
        Subsignal("n", Pins("E6")),
    ),
    ("rst_n", Pins("A13"), IOStandard("LVCMOS33")),
    ("user_led", 0, Pins("D22"), IOStandard("LVCMOS33")),
    ("serial", 0,
        Subsignal("tx", Pins("A18")),
        Subsignal("rx", Pins("A19")),
        IOStandard("LVCMOS33")
    ),
    ("sfp_tx_disable_n", 0, Pins("D20"), IOStandard("LVCMOS33")),
    ("sfp_tx", 0,
        Subsignal("p", Pins("D5")),
        Subsignal("n", Pins("C5")),
    ),
    ("sfp_rx", 0,
        Subsignal("p", Pins("D11")),
        Subsignal("n", Pins("C11")),
    ),
]


class Platform(XilinxPlatform):
    default_clk_name = "clk100"
    default_clk_period = 10.0

    def __init__(self, toolchain="vivado", programmer="vivado"):
        XilinxPlatform.__init__(self, "xc7a35t-fgg484-2", _io,
                                toolchain=toolchain)

    def create_programmer(self):
        return VivadoProgrammer()


class BaseSoC(SoCCore):
    def __init__(self, platform):
        sys_clk = Signal()
        sys_clk_freq = int(62.5e6)
        SoCCore.__init__(self, platform, sys_clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTP PCIE SDR Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(sys_clk)
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  sys_clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        refclk = Signal()
        refclk_pads = platform.request("clk100")
        self.specials += [
            Instance("IBUFDS_GTE2",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk),
            Instance("BUFG", i_I=refclk, o_O=sys_clk)
        ]

        qpll = GTPQuadPLL(refclk, 100e6, 1.25e9)
        print(qpll)
        self.submodules += qpll

        self.comb += platform.request("sfp_tx_disable_n").eq(1)
        tx_pads = platform.request("sfp_tx")
        rx_pads = platform.request("sfp_rx")

        gtp = GTP(qpll, tx_pads, rx_pads, sys_clk_freq,
            clock_aligner=True, internal_loopback=False)
        self.submodules += gtp

        counter = Signal(32)
        self.sync += counter.eq(counter + 1)

        self.comb += [
            gtp.encoder.k[0].eq(1),
            gtp.encoder.d[0].eq((5 << 5) | 28),
            gtp.encoder.k[1].eq(0),
            gtp.encoder.d[1].eq(counter[26:]),
        ]

        self.crg.cd_sys.clk.attr.add("keep")
        gtp.cd_rtio.clk.attr.add("keep")
        gtp.cd_rtio_rx.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 16)
        platform.add_period_constraint(gtp.cd_rtio.clk, 1e9/gtp.rtio_clk_freq)
        platform.add_period_constraint(gtp.cd_rtio_rx.clk, 1e9/gtp.rtio_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gtp.cd_rtio.clk,
            gtp.cd_rtio_rx.clk)

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 0).eq(rtio_counter[26])


def main():
    platform = Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build_pcie_sdr", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
