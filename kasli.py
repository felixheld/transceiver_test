#!/usr/bin/env python3
import sys

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.gtp_7series import GTPQuadPLL, GTP

from litescope import LiteScopeAnalyzer


_io = [
    ("clk50", 0, Pins("W19"), IOStandard("LVCMOS25")),
    ("user_led", 0, Pins("T16"), IOStandard("LVCMOS25")),
    ("user_led", 1, Pins("P16"), IOStandard("LVCMOS25")), # sfp_ctl0
    ("user_led", 2, Pins("R19"), IOStandard("LVCMOS25")), # sfp_ctl1
    ("user_led", 3, Pins("P19"), IOStandard("LVCMOS25")), # sfp_ctl2
    ("serial", 0,
        Subsignal("rx", Pins("N13")),
        Subsignal("tx", Pins("N17")),
        IOStandard("LVCMOS25")
    ),
    ("sfp_tx_disable_n", 0, Pins("R14"), IOStandard("LVCMOS25")),
    ("sfp_tx", 0,
        Subsignal("p", Pins("D5")),
        Subsignal("n", Pins("C5")),
    ),
    ("sfp_rx", 0,
        Subsignal("p", Pins("D11")),
        Subsignal("n", Pins("C11")),
    ),
    ("sfp_tx_disable_n", 2, Pins("V17"), IOStandard("LVCMOS25")),
    ("sfp_tx", 2,
        Subsignal("p", Pins("B6")),
        Subsignal("n", Pins("A6")),
    ),
    ("sfp_rx", 2,
        Subsignal("p", Pins("B10")),
        Subsignal("n", Pins("A10")),
    ),
]


class Platform(XilinxPlatform):
    default_clk_name = "clk50"
    default_clk_period = 20.0

    def __init__(self, toolchain="vivado", programmer="vivado"):
        XilinxPlatform.__init__(self, "xc7a100t-fgg484-2", _io,
                                toolchain=toolchain)

    def create_programmer(self):
        return VivadoProgrammer()


class BaseSoC(SoCCore):
    def __init__(self, platform):
        self.sys_clk = Signal()
        self.sys_clk_freq = int(50e6)
        SoCCore.__init__(self, platform, self.sys_clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="Kasli Transceiver Test Design",
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
    def __init__(self, platform, medium="sfp2", loopback=False, with_analyzer=True):
        BaseSoC.__init__(self, platform)

        refclk50 = platform.request("clk50")
        self.specials += [
            Instance("BUFG", i_I=refclk50, o_O=self.sys_clk)
        ]

        refclk150 = Signal()
        refclk150_bufg = Signal()
        pll_fb = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                p_STARTUP_WAIT="FALSE", #o_LOCKED=,

                # VCO @ 1.2GHz
                p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=20.0,
                p_CLKFBOUT_MULT=24, p_DIVCLK_DIVIDE=1,
                i_CLKIN1=self.sys_clk, i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb,

                # 150MHz
                p_CLKOUT0_DIVIDE=8, p_CLKOUT0_PHASE=0.0, o_CLKOUT0=refclk150
            ),
            Instance("BUFG", i_I=refclk150, o_O=refclk150_bufg)
        ]
        platform.add_platform_command("set_property SEVERITY {{Warning}} [get_drc_checks REQP-49]")

        qpll = GTPQuadPLL(refclk150_bufg, 150e6, 3.0e9)
        print(qpll)
        self.submodules += qpll

        if medium == "sfp0":
            self.comb += platform.request("sfp_tx_disable_n", 0).eq(1)
            tx_pads = platform.request("sfp_tx", 0)
            rx_pads = platform.request("sfp_rx", 0)
        elif medium == "sfp2":
            self.comb += platform.request("sfp_tx_disable_n", 2).eq(1)
            tx_pads = platform.request("sfp_tx", 2)
            rx_pads = platform.request("sfp_rx", 2)
        else:
            raise ValueError
        gtp = GTP(qpll, tx_pads, rx_pads, self.sys_clk_freq,
            clock_aligner=True, internal_loopback=False)
        self.submodules += gtp

        counter = Signal(32)
        self.sync.tx += counter.eq(counter + 1)

        self.comb += [
            gtp.encoder.k[0].eq(1),
            gtp.encoder.d[0].eq((5 << 5) | 28),
            gtp.encoder.k[1].eq(0)
        ]
        if loopback:
            self.comb += gtp.encoder.d[1].eq(gtp.decoders[1].d)
        else:
            self.comb += gtp.encoder.d[1].eq(counter[26:])

        self.crg.cd_sys.clk.attr.add("keep")
        gtp.cd_tx.clk.attr.add("keep")
        gtp.cd_rx.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 20)
        platform.add_period_constraint(gtp.cd_tx.clk, 1e9/gtp.tx_clk_freq)
        platform.add_period_constraint(gtp.cd_rx.clk, 1e9/gtp.tx_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gtp.cd_tx.clk,
            gtp.cd_rx.clk)

        tx_counter_led = Signal()
        tx_counter = Signal(32)
        self.sync.tx += tx_counter.eq(tx_counter + 1)
        self.comb += tx_counter_led.eq(tx_counter[26])

        rx_counter_led = Signal()
        rx_counter = Signal(32)
        self.sync.rx += rx_counter.eq(rx_counter + 1)
        self.comb += rx_counter_led.eq(rx_counter[26])

        self.comb += platform.request("user_led", 0).eq(tx_counter_led ^ rx_counter_led)
        for i in range(3):
            self.comb += platform.request("user_led", i + 1).eq(gtp.decoders[1].d[i])

        if with_analyzer:
            analyzer_signals = [
                gtp.tx_init.restart,
                gtp.rx_init.restart,
                gtp.decoders[0].input,
                gtp.decoders[0].d,
                gtp.decoders[0].k,
                gtp.decoders[1].input,
                gtp.decoders[1].d,
                gtp.decoders[1].k,
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 256, cd_ratio=4, cd="rx")

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")


def main():
    platform = Platform()
    if len(sys.argv) < 2:
        print("missing target (base or gtp)")
        exit()
    if sys.argv[1] == "base":
        soc = BaseSoC(platform)
    elif sys.argv[1] == "gtp":
        soc = GTPTestSoC(platform)
    builder = Builder(soc, output_dir="build_kasli", csr_csv="test/csr.csv")
    vns = builder.build()
    soc.do_exit(vns)


if __name__ == "__main__":
    main()
