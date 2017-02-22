#!/usr/bin/env python3

from litex.gen import *
from litex.boards.platforms import kc705

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from litex.build.generic_platform import *
from litex.boards.platforms import kc705

from transceiver.gtx_7series import GTXChannelPLL, GTX


class BaseSoC(SoCCore):
    def __init__(self, platform, medium="sfp"):
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
                o_ODIV2=refclk)
        ]

        rtio_linerate = 1.25e9
        rtio_clk_freq = 62.5e6

        cpll = GTXChannelPLL(refclk, rtio_clk_freq, rtio_linerate)
        print(cpll)
        self.submodules += cpll

        if medium == "sfp":
            self.comb += platform.request("sfp_tx_disable_n").eq(1)
            tx_pads = platform.request("sfp_tx")
            rx_pads = platform.request("sfp_rx")
            polarity = 1 # https://www.xilinx.com/support/answers/46614.html...
        elif medium == "sma":
            tx_pads = platform.request("user_sma_mgt_tx")
            rx_pads = platform.request("user_sma_mgt_rx")
            polarity = 0
        else:
            raise ValueError
        gtx = GTX(cpll, tx_pads, rx_pads, clk_freq,
            tx_polarity=polarity, rx_polarity=polarity)
        self.submodules += gtx

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

        platform.add_period_constraint(self.crg.cd_sys.clk, platform.default_clk_period)
        platform.add_period_constraint(gtx.txoutclk, 1/rtio_clk_freq)
        platform.add_period_constraint(gtx.rxoutclk, 1/rtio_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gtx.txoutclk,
            gtx.rxoutclk)

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 7).eq(rtio_counter[26])

        rtio_rx_counter = Signal(32)
        self.sync.rtio_rx += rtio_rx_counter.eq(rtio_rx_counter + 1)
        self.comb += platform.request("user_led", 6).eq(rtio_rx_counter[26])


def main():
    platform = kc705.Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build_kc705", csr_csv="test/csr.csv")
    builder.build()

if __name__ == "__main__":
    main()
