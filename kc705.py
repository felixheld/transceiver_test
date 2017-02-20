#!/usr/bin/env python3

from litex.gen import *
from litex.boards.platforms import kc705

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from litex.build.generic_platform import *
from litex.boards.platforms import kc705

from gtx_7series import GTXChannelPLL, GTX


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(1e9/platform.default_clk_period)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTX KC705 Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        refclk = Signal()
        refclk_pads = platform.request("sgmii_clock")
        self.specials += [
            Instance("IBUFDS_GTE2",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk)
        ]
        cpll = GTXChannelPLL(refclk, 125e6, 1.25e9)
        print(cpll)
        gtx = GTX(cpll,
                  platform.request("sfp_tx"),
                  platform.request("sfp_rx"),
                  clk_freq)
        self.submodules += cpll, gtx

        counter = Signal(32)
        self.sync += counter.eq(counter + 1)

        self.comb += [
            gtx.encoder.k[0].eq(1),
            gtx.encoder.d[0].eq((5 << 5) | 28),
            gtx.encoder.k[1].eq(0),
            gtx.encoder.d[1].eq(counter[26:]),
        ]

        self.comb += platform.request("user_led", 4).eq(gtx.rx_ready)
        for i in range(4):
            self.comb += platform.request("user_led", i).eq(gtx.decoders[1].d[i])

        self.comb += platform.request("sfp_tx_disable_n").eq(1)

        platform.add_period_constraint(gtx.txoutclk, 8.0)
        platform.add_period_constraint(gtx.rxoutclk, 8.0)


def main():
    platform = kc705.Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build_kc705", csr_csv="test/csr.csv")
    builder.build()

if __name__ == "__main__":
    main()
