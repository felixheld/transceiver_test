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
        cpll = GTHChannelPLL(refclk, 125e6, 1.25e9)
        print(cpll)
        gtx = GTH(cpll,
                  platform.request("sfp_tx"),
                  platform.request("sfp_rx"),
                  clk_freq)
        self.submodules += cpll, gtx

        self.comb += [
            gtx.encoder.k[0].eq(1),
            gtx.encoder.d[0].eq((5 << 5) | 28),
            gtx.encoder.k[1].eq(0),
            gtx.encoder.d[1].eq(0x5a),
        ]

        self.comb += platform.request("sfp_tx_disable_n").eq(1)

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 7).eq(rtio_counter[26])

        sys_counter = Signal(32)
        self.sync += sys_counter.eq(sys_counter + 1)
        self.comb += platform.request("user_led", 6).eq(sys_counter[26])


def main():
    platform = kcu105.Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
