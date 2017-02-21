#!/usr/bin/env python3

from litex.gen import *
from litex.boards.platforms import kcu105

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from gth_ultrascale import GTHChannelPLL, GTH


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(1e9/platform.default_clk_period)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTX KCU105 Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        # 125MHz clock -> user_sma --> user_sma_mgt_refclk
        user_sma_clock_pads = platform.request("user_sma_clock")
        user_sma_clock = Signal()
        self.specials += [
            Instance("ODDRE1",
                i_D1=0, i_D2=1, i_SR=0,
                i_C=ClockSignal(),
                o_Q=user_sma_clock),
            Instance("OBUFDS",
                i_I=user_sma_clock,
                o_O=user_sma_clock_pads.p,
                o_OB=user_sma_clock_pads.n)
        ]

        refclk = Signal()
        refclk_pads = platform.request("user_sma_mgt_refclk")
        self.specials += [
            Instance("IBUFDS_GTE3",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk)
        ]
        rtio_linerate = 1.25e9
        rtio_clk_freq = 125e6
        cpll = GTHChannelPLL(refclk, rtio_clk_freq, rtio_linerate)
        print(cpll)
        gth = GTH(cpll,
                  platform.request("sfp_tx"),
                  platform.request("sfp_rx"),
                  clk_freq)
        self.submodules += cpll, gth

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 7).eq(rtio_counter[26])

        sys_counter = Signal(32)
        self.sync += sys_counter.eq(sys_counter + 1)
        self.comb += platform.request("user_led", 6).eq(sys_counter[26])

        self.comb += [
            gth.encoder.k[0].eq(1),
            gth.encoder.d[0].eq((5 << 5) | 28),
            gth.encoder.k[1].eq(0),
            gth.encoder.d[1].eq(sys_counter[26:]),
        ]

        for i in range(4):
            self.comb += platform.request("user_led", i).eq(gth.encoder.d[1][i])

        self.comb += platform.request("sfp_tx_disable_n").eq(1)

        platform.add_period_constraint(self.crg.cd_sys.clk, platform.default_clk_period)
        platform.add_period_constraint(gth.txoutclk, 1/rtio_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gth.txoutclk)


def main():
    platform = kcu105.Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build_kcu105", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
