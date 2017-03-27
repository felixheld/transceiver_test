#!/usr/bin/env python3

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer
from litex.build.generic_platform import *
from litex.boards.platforms import arty

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from transceiver.serdes_7series import SERDESPLL, SERDES

serdes_io = [
    ("serdes_clk", 0, # JC1
        Subsignal("p", Pins("U12")),
        Subsignal("n", Pins("V12")),
        IOStandard("TMDS_33"),
    ),
    ("serdes_tx", 0, # JC2
        Subsignal("p", Pins("V10")),
        Subsignal("n", Pins("V11")),
        IOStandard("TMDS_33"),
    ),
    ("serdes_rx", 0, # JC4
        Subsignal("p", Pins("T13")),
        Subsignal("n", Pins("U13")),
        IOStandard("TMDS_33"),
    ),
]


class _CRG(Module):
    def __init__(self, platform):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_clk125 = ClockDomain()
        self.clock_domains.cd_clk200 = ClockDomain()

        clk100 = platform.request("clk100")

        pll_locked = Signal()
        pll_fb = Signal()
        pll_sys = Signal()
        pll_clk200 = Signal()
        pll_clk125 = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                     p_STARTUP_WAIT="FALSE", o_LOCKED=pll_locked,

                     # VCO @ 1000 MHz
                     p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=10.0,
                     p_CLKFBOUT_MULT=10, p_DIVCLK_DIVIDE=1,
                     i_CLKIN1=clk100, i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb,

                     # 100 MHz
                     p_CLKOUT0_DIVIDE=10, p_CLKOUT0_PHASE=0.0,
                     o_CLKOUT0=pll_sys,

                     # 200 MHz
                     p_CLKOUT1_DIVIDE=5, p_CLKOUT1_PHASE=0.0,
                     o_CLKOUT1=pll_clk200,

                     # 125 MHz
                     p_CLKOUT2_DIVIDE=8, p_CLKOUT2_PHASE=0.0,
                     o_CLKOUT2=pll_clk125,

            ),
            Instance("BUFG", i_I=pll_sys, o_O=self.cd_sys.clk),
            Instance("BUFG", i_I=pll_clk200, o_O=self.cd_clk200.clk),
            Instance("BUFG", i_I=pll_clk125, o_O=self.cd_clk125.clk),
            AsyncResetSynchronizer(self.cd_sys, ~pll_locked),
            AsyncResetSynchronizer(self.cd_clk200, ~pll_locked),
            AsyncResetSynchronizer(self.cd_clk125, ~pll_locked)
        ]

        reset_counter = Signal(4, reset=15)
        ic_reset = Signal(reset=1)
        self.sync.clk200 += \
            If(reset_counter != 0,
                reset_counter.eq(reset_counter - 1)
            ).Else(
                ic_reset.eq(0)
            )
        self.specials += Instance("IDELAYCTRL", i_REFCLK=ClockSignal("clk200"), i_RST=ic_reset)


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(100e6)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTX KC705 Test Design",
            with_timer=False
        )
        self.submodules.crg = _CRG(platform)
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)


class SERDESTestSoC(BaseSoC):
    def __init__(self, platform):
        BaseSoC.__init__(self, platform)

        pll = SERDESPLL(125e6, 1.25e9)
        self.comb += pll.refclk.eq(self.crg.cd_clk125.clk)
        self.submodules += pll

        clock_pads = platform.request("serdes_clk")
        tx_pads = platform.request("serdes_tx")
        rx_pads = platform.request("serdes_rx")
        serdes = SERDES(pll, clock_pads, tx_pads, rx_pads, mode="master")
        #self.comb += serdes.produce_square_wave.eq(1)
        self.submodules += serdes

        self.crg.cd_sys.clk.attr.add("keep")
        serdes.cd_rtio.clk.attr.add("keep")
        serdes.cd_serdes.clk.attr.add("keep")
        serdes.cd_serdes_div.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 10.0),
        platform.add_period_constraint(serdes.cd_rtio.clk, 16.0),
        platform.add_period_constraint(serdes.cd_serdes.clk, 1.6),
        platform.add_period_constraint(serdes.cd_serdes_div.clk, 6.4)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            serdes.cd_rtio.clk,
            serdes.cd_serdes.clk,
            serdes.cd_serdes_div.clk)

        counter = Signal(32)
        self.sync.rtio += counter.eq(counter + 1)
        self.comb += [
            serdes.encoder.d[0].eq(counter),
            serdes.encoder.d[1].eq(counter)
        ]

        sys_counter = Signal(32)
        self.sync.sys += sys_counter.eq(sys_counter + 1)
        self.comb += platform.request("user_led", 0).eq(sys_counter[26])

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 1).eq(rtio_counter[26])

        serdes_div_counter = Signal(32)
        self.sync.serdes_div += serdes_div_counter.eq(serdes_div_counter + 1)
        self.comb += platform.request("user_led", 2).eq(serdes_div_counter[26])

        serdes_counter = Signal(32)
        self.sync.serdes += serdes_counter.eq(serdes_counter + 1)
        self.comb += platform.request("user_led", 3).eq(serdes_counter[26])


def main():
    platform = arty.Platform()
    platform.add_extension(serdes_io)
    soc = SERDESTestSoC(platform)
    builder = Builder(soc, output_dir="build_arty", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
