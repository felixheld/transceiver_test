#!/usr/bin/env python3

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from transceiver.gtp_7series import GTPQuadPLL, GTP

from litescope import LiteScopeAnalyzer


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
        self.sys_clk = Signal()
        self.sys_clk_freq = int(100e6)
        SoCCore.__init__(self, platform, self.sys_clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTP PCIE SDR Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(self.sys_clk)
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  self.sys_clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)


class GTPTestSoC(BaseSoC):
    csr_map = {
        "analyzer": 20
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform):
        BaseSoC.__init__(self, platform)

        refclk100 = Signal()
        refclk100_pads = platform.request("clk100")
        self.specials += [
            Instance("IBUFDS_GTE2",
                i_CEB=0,
                i_I=refclk100_pads.p,
                i_IB=refclk100_pads.n,
                o_O=refclk100),
            Instance("BUFG", i_I=refclk100, o_O=self.sys_clk)
        ]

        refclk125 = Signal()
        pll_fb = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                p_STARTUP_WAIT="FALSE", #o_LOCKED=,

                # VCO @ 1GHz
                p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=10.0,
                p_CLKFBOUT_MULT=10, p_DIVCLK_DIVIDE=1,
                i_CLKIN1=self.sys_clk, i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb,

                # 125MHz
                p_CLKOUT0_DIVIDE=8, p_CLKOUT0_PHASE=0.0, o_CLKOUT0=refclk125
            ),
        ]
        platform.add_platform_command("set_property SEVERITY {{Warning}} [get_drc_checks REQP-49]")

        qpll = GTPQuadPLL(refclk125, 125e6, 1.25e9)
        print(qpll)
        self.submodules += qpll

        self.comb += platform.request("sfp_tx_disable_n").eq(1)
        tx_pads = platform.request("sfp_tx")
        rx_pads = platform.request("sfp_rx")

        gtp = GTP(qpll, tx_pads, rx_pads, self.sys_clk_freq,
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
        platform.add_period_constraint(self.crg.cd_sys.clk, 10)
        platform.add_period_constraint(gtp.cd_rtio.clk, 1e9/gtp.rtio_clk_freq)
        platform.add_period_constraint(gtp.cd_rtio_rx.clk, 1e9/gtp.rtio_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gtp.cd_rtio.clk,
            gtp.cd_rtio_rx.clk)

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 0).eq(rtio_counter[26])

        analyzer_signals = [
            gtp.decoders[0].input,
            gtp.decoders[0].d,
            gtp.decoders[0].k,
            gtp.decoders[1].input,
            gtp.decoders[1].d,
            gtp.decoders[1].k,
        ]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 2048, cd="sys")

    def do_exit(self, vns):
        self.analyzer.export_csv(vns, "test/analyzer.csv")


def main():
    platform = Platform()
    soc = GTPTestSoC(platform)
    builder = Builder(soc, output_dir="build_pcie_sdr", csr_csv="test/csr.csv")
    vns = builder.build()
    soc.do_exit(vns)


if __name__ == "__main__":
    main()
