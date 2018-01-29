#!/usr/bin/env python3
import sys

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform, VivadoProgrammer

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.gtp_7series_artiq import *

from litescope import LiteScopeAnalyzer


_io = [
    ("clk100", 0, Pins("R4"), IOStandard("LVCMOS25")),
    ("rst_n", Pins("AA1"), IOStandard("LVCMOS25")),
    ("user_led", 0, Pins("AB1"), IOStandard("LVCMOS25")),
    ("user_led", 1, Pins("AB8"), IOStandard("LVCMOS25")),
    ("user_btn", 0, Pins("AA1"), IOStandard("LVCMOS25")),
    ("user_btn", 1, Pins("AB6"), IOStandard("LVCMOS25")),
    ("serial", 0,
        Subsignal("tx", Pins("Y6")),
        Subsignal("rx", Pins("AA6")),
        IOStandard("LVCMOS25")
    ),
    ("sfp_tx_disable_n", 0, Pins("AA20"), IOStandard("LVCMOS25")),
    ("sfp_data", 0,
        Subsignal("txp", Pins("D5")),
        Subsignal("txn", Pins("C5")),
        Subsignal("rxp", Pins("D11")),
        Subsignal("rxn", Pins("C11")),
    ),
    ("sfp_tx_disable_n", 1, Pins("V17"), IOStandard("LVCMOS25")),
    ("sfp_data", 1,
        Subsignal("txp", Pins("D7")),
        Subsignal("txn", Pins("C7")),
        Subsignal("rxp", Pins("D9")),
        Subsignal("rxn", Pins("C9")),
    ),
]


class Platform(XilinxPlatform):
    default_clk_name = "clk100"
    default_clk_period = 10.0

    def __init__(self, toolchain="vivado", programmer="vivado"):
        XilinxPlatform.__init__(self, "xc7a50t-fgg484-2", _io,
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
            ident="PCIe CPRI Transceiver Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(self.sys_clk, ~platform.request("user_btn", 0))
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  self.sys_clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)


class GTPTestSoC(BaseSoC):
    csr_map = {
        "analyzer": 20
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform, medium="sfp0", loopback=False, with_analyzer=True):
        BaseSoC.__init__(self, platform)

        refclk100 = platform.request("clk100")
        self.specials += [
            Instance("BUFG", i_I=refclk100, o_O=self.sys_clk)
        ]

        refclk150 = Signal()
        refclk150_bufg = Signal()
        pll_fb = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                p_STARTUP_WAIT="FALSE", #o_LOCKED=,

                # VCO @ 1.2GHz
                p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=10.0,
                p_CLKFBOUT_MULT=12, p_DIVCLK_DIVIDE=1,
                i_CLKIN1=self.sys_clk, i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb,

                # 150MHz
                p_CLKOUT0_DIVIDE=8, p_CLKOUT0_PHASE=0.0, o_CLKOUT0=refclk150
            ),
            Instance("BUFG", i_I=refclk150, o_O=refclk150_bufg)
        ]
        platform.add_platform_command("set_property SEVERITY {{Warning}} [get_drc_checks REQP-49]")

        qpll_settings = QPLLSettings(
            refclksel=0b001,
            fbdiv=5,
            fbdiv_45=4,
            refclk_div=1)
        qpll = QPLL(refclk150_bufg, qpll_settings)
        self.submodules += qpll

        if medium == "sfp0":
            self.comb += platform.request("sfp_tx_disable_n", 0).eq(1)
            data_pads = platform.request("sfp_data", 0)
        elif medium == "sfp1":
            self.comb += platform.request("sfp_tx_disable_n", 1).eq(1)
            data_pads = platform.request("sfp_data", 1)
        else:
            raise ValueError
        rtio_clk_freq = 3.0e9//20
        gtp = GTPSingle(qpll.channels[0], data_pads, self.sys_clk_freq, rtio_clk_freq, mode="master")
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
            self.comb += gtp.encoder.d[1].eq(counter)

        self.crg.cd_sys.clk.attr.add("keep")
        gtp.cd_tx.clk.attr.add("keep")
        gtp.cd_rx.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 10)
        platform.add_period_constraint(gtp.cd_tx.clk, 1e9/rtio_clk_freq)
        platform.add_period_constraint(gtp.cd_rx.clk, 1e9/rtio_clk_freq)
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

        self.comb += platform.request("user_led", 0).eq(tx_counter_led)
        self.comb += platform.request("user_led", 1).eq(rx_counter_led)

        if with_analyzer:
            analyzer_signals = [
                gtp.decoders[0].input,
                gtp.decoders[0].d,
                gtp.decoders[0].k,
                gtp.decoders[1].input,
                gtp.decoders[1].d,
                gtp.decoders[1].k,
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 256, cd_ratio=2, cd="tx")

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
    builder = Builder(soc, output_dir="build_pcie_cpri", csr_csv="test/csr.csv")
    vns = builder.build()
    soc.do_exit(vns)


if __name__ == "__main__":
    main()
