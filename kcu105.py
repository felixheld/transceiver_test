#!/usr/bin/env python3

from litex.gen import *
from litex.boards.platforms import kcu105

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.gth_ultrascale import GTHChannelPLL, GTH
from transceiver.serdes_ultrascale import SERDESPLL, SERDES


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


class GTHTestSoC(BaseSoC):
    def __init__(self, platform, medium="sfp0"):
        BaseSoC.__init__(self, platform)

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
        self.submodules += cpll

        if medium == "sfp0":
            self.comb += platform.request("sfp_tx_disable_n", 0).eq(1)
            tx_pads = platform.request("sfp_tx", 0)
            rx_pads = platform.request("sfp_rx", 0)
        elif medium == "sfp1":
            self.comb += platform.request("sfp_tx_disable_n", 1).eq(1)
            tx_pads = platform.request("sfp_tx", 1)
            rx_pads = platform.request("sfp_rx", 1)
        elif medium == "sma":
            tx_pads = platform.request("user_sma_mgt_tx")
            rx_pads = platform.request("user_sma_mgt_rx")
        else:
            raise ValueError
        gth = GTH(cpll, tx_pads, rx_pads, self.clk_freq,
            clock_aligner=True, internal_loopback=False)
        self.submodules += gth

        counter = Signal(32)
        self.sync.rtio += counter.eq(counter + 1)

        self.comb += [
            gth.encoder.k[0].eq(1),
            gth.encoder.d[0].eq((5 << 5) | 28),
            gth.encoder.k[1].eq(0),
            gth.encoder.d[1].eq(counter[26:]),
        ]

        self.comb += platform.request("user_led", 4).eq(gth.rx_ready)
        for i in range(4):
            self.comb += platform.request("user_led", i).eq(gth.decoders[1].d[i])

        self.crg.cd_sys.clk.attr.add("keep")
        gth.cd_rtio.clk.attr.add("keep")
        gth.cd_rtio_rx.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, platform.default_clk_period)
        platform.add_period_constraint(gth.cd_rtio.clk, 1e9/gth.rtio_clk_freq)
        platform.add_period_constraint(gth.cd_rtio_rx.clk, 1e9/gth.rtio_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gth.cd_rtio.clk,
            gth.cd_rtio_rx.clk)

        rtio_counter = Signal(32)
        self.sync.rtio += rtio_counter.eq(rtio_counter + 1)
        self.comb += platform.request("user_led", 7).eq(rtio_counter[26])

        rtio_rx_counter = Signal(32)
        self.sync.rtio_rx += rtio_rx_counter.eq(rtio_rx_counter + 1)
        self.comb += platform.request("user_led", 6).eq(rtio_rx_counter[26])


class SERDESTestSoC(BaseSoC):
    def __init__(self, platform):
        BaseSoC.__init__(self, platform)

        pll = SERDESPLL(125e6, 1.25e9)
        self.comb += pll.refclk.eq(ClockSignal())
        self.submodules += pll

        tx_pads = platform.request("user_sma_clock")
        serdes = SERDES(pll, tx_pads)
        self.submodules += serdes

        counter = Signal(32)
        self.sync += counter.eq(counter + 1)

        self.comb += [
            serdes.encoder.k[0].eq(1),
            serdes.encoder.d[0].eq((5 << 5) | 28),
            serdes.encoder.k[1].eq(0),
            serdes.encoder.d[1].eq(counter[26:]),
        ]


def main():
    platform = kcu105.Platform()
    soc = GTHTestSoC(platform)
    #soc = SERDESTestSoC(platform)
    builder = Builder(soc, output_dir="build_kcu105", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
