#!/usr/bin/env python3
import sys

from migen import *
from litex.soc.interconnect.csr import *
from litex.build.generic_platform import *
from litex.boards.platforms import kcu105

from migen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.gth_ultrascale import GTHChannelPLL, GTH, MultiGTH
from transceiver.serdes_ultrascale import SERDESPLL, SERDES

from litescope import LiteScopeAnalyzer


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(1e9/platform.default_clk_period)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="Transceiver Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        self.crg.cd_sys.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 8.0)


class GTHTestSoC(BaseSoC):
    def __init__(self, platform, medium="sfp0"):
        BaseSoC.__init__(self, platform)

        # 300Mhz clock -> user_sma --> user_sma_mgt_refclk
        clk300 = platform.request("clk300")
        clk300_se = Signal()
        self.specials += Instance("IBUFDS", i_I=clk300.p, i_IB=clk300.n, o_O=clk300_se)
        user_sma_clock_pads = platform.request("user_sma_clock")
        user_sma_clock = Signal()
        self.specials += [
            Instance("ODDRE1",
                i_D1=0, i_D2=1, i_SR=0,
                i_C=clk300_se,
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

        cpll = GTHChannelPLL(refclk, 300e6, 3.0e9)
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
        self.sync.tx += counter.eq(counter + 1)

        self.comb += [
            gth.encoder.k[0].eq(1),
            gth.encoder.d[0].eq((5 << 5) | 28),
            gth.encoder.k[1].eq(0),
            gth.encoder.d[1].eq(counter[26:]),
        ]

        self.comb += platform.request("user_led", 4).eq(gth.rx_ready)
        for i in range(4):
            self.comb += platform.request("user_led", i).eq(gth.decoders[1].d[i])

        gth.cd_tx.clk.attr.add("keep")
        gth.cd_rx.clk.attr.add("keep")
        platform.add_period_constraint(gth.cd_tx.clk, 1e9/gth.tx_clk_freq)
        platform.add_period_constraint(gth.cd_rx.clk, 1e9/gth.tx_clk_freq)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            gth.cd_tx.clk,
            gth.cd_rx.clk)

        tx_counter = Signal(32)
        self.sync.tx += tx_counter.eq(tx_counter + 1)
        self.comb += platform.request("user_led", 7).eq(tx_counter[26])

        rx_counter = Signal(32)
        self.sync.rx += rx_counter.eq(rx_counter + 1)
        self.comb += platform.request("user_led", 6).eq(rx_counter[26])


multigt_io = [
    ("sfps_tx", 0,
        Subsignal("p", Pins("U4 W4")),
        Subsignal("n", Pins("U3 W3"))
    ),
    ("sfps_rx", 0,
        Subsignal("p", Pins("T2 V2")),
        Subsignal("n", Pins("T1 V1"))
    ),
    ("sfps_tx_disable_n", 0, Pins("AL8 D28"), IOStandard("LVCMOS18")),
]


class MultiGTHTestSoC(BaseSoC):
    def __init__(self, platform):
        BaseSoC.__init__(self, platform)
        platform.add_extension(multigt_io)

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

        cplls = [GTHChannelPLL(refclk, 125e6, 1.25e9) for i in range(2)]
        self.submodules += iter(cplls)
        print(cplls)

        self.comb += platform.request("sfps_tx_disable_n").eq(0b11)
        tx_pads = platform.request("sfps_tx")
        rx_pads = platform.request("sfps_rx")
        mgth = MultiGTH(cplls, tx_pads, rx_pads, self.clk_freq,
            clock_aligner=True, internal_loopback=False)
        self.submodules += mgth

        counter = Signal(32)
        self.sync.gth0_tx += counter.eq(counter + 1)

        self.comb += [
            mgth.encoders[0].k.eq(1),
            mgth.encoders[0].d.eq((5 << 5) | 28),
            mgth.encoders[1].k.eq(0),
            mgth.encoders[1].d.eq(counter[26:]),
            mgth.encoders[2].k.eq(1),
            mgth.encoders[2].d.eq((5 << 5) | 28),
            mgth.encoders[3].k.eq(0),
            mgth.encoders[3].d.eq(counter[26:]),
        ]

        self.comb += platform.request("user_led", 4).eq(mgth.rx_ready)
        led_mode = platform.request("user_dip_btn", 0)
        for i in range(4):
            user_led = platform.request("user_led", i)
            self.comb += \
                If(led_mode,
                    user_led.eq(mgth.decoders[3].d[i])
                ).Else(
                    user_led.eq(mgth.decoders[1].d[i])
                )
        for i in range(mgth.nlanes):
            gth = mgth.gths[i]
            gth.cd_tx.clk.attr.add("keep")
            gth.cd_rx.clk.attr.add("keep")
            platform.add_period_constraint(gth.cd_tx.clk, 1e9/gth.tx_clk_freq)
            platform.add_period_constraint(gth.cd_rx.clk, 1e9/gth.tx_clk_freq)
            self.platform.add_false_path_constraints(
                self.crg.cd_sys.clk,
                gth.cd_tx.clk,
                gth.cd_rx.clk)

        tx_counter = Signal(32)
        self.sync.gth0_tx += tx_counter.eq(tx_counter + 1)
        self.comb += platform.request("user_led", 7).eq(tx_counter[26])

        rx_counter0 = Signal(32)
        rx_counter1 = Signal(32)
        self.sync.gth0_rx += rx_counter0.eq(rx_counter0 + 1)
        self.sync.gth1_rx += rx_counter0.eq(rx_counter1 + 1)
        user_led = platform.request("user_led", 6)
        self.comb += \
            If(led_mode,
                user_led.eq(rx_counter0[26])
            ).Else(
                user_led.eq(rx_counter1[26])
            )


serdes_io = [
    # cyusb3acc_005 fmc with loopback
    ("master_serdes", 0,
        Subsignal("clk_p", Pins("LPC:LA20_P"), Misc("DIFF_TERM=TRUE")), # g21
        Subsignal("clk_n", Pins("LPC:LA20_N"), Misc("DIFF_TERM=TRUE")), # g22
        Subsignal("tx_p", Pins("LPC:LA22_P"), Misc("DIFF_TERM=TRUE")), # g24
        Subsignal("tx_n", Pins("LPC:LA22_N"), Misc("DIFF_TERM=TRUE")), # g25
        Subsignal("rx_p", Pins("LPC:LA11_P"), Misc("DIFF_TERM=TRUE")), # h16
        Subsignal("rx_n", Pins("LPC:LA11_N"), Misc("DIFF_TERM=TRUE")), # h17
        IOStandard("LVDS"),
    ),

    ("slave_serdes", 0,
        Subsignal("clk_p", Pins("LPC:LA04_P"), Misc("DIFF_TERM=TRUE")), # h10
        Subsignal("clk_n", Pins("LPC:LA04_P"), Misc("DIFF_TERM=TRUE")), # h11
        Subsignal("tx_p", Pins("LPC:LA25_P"), Misc("DIFF_TERM=TRUE")), # g27
        Subsignal("tx_n", Pins("LPC:LA25_N"), Misc("DIFF_TERM=TRUE")), # g28
        Subsignal("rx_p", Pins("LPC:LA07_P"), Misc("DIFF_TERM=TRUE")), # h13
        Subsignal("rx_n", Pins("LPC:LA07_N"), Misc("DIFF_TERM=TRUE")), # h14
        IOStandard("LVDS"),
    ),
]


class SERDESTestSoC(BaseSoC):
    csr_map = {
        "master_serdes": 20,
        "slave_serdes": 21,
        "analyzer": 22
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform, analyzer=None):
        BaseSoC.__init__(self, platform)

        # master

        master_pll = SERDESPLL(125e6, 1.25e9)
        self.comb += master_pll.refclk.eq(ClockSignal())
        self.submodules += master_pll

        master_pads = platform.request("master_serdes")
        self.submodules.master_serdes = master_serdes = SERDES(
            master_pll, master_pads, mode="master")

        master_serdes.cd_serdes.clk.attr.add("keep")
        master_serdes.cd_serdes_10x.clk.attr.add("keep")
        master_serdes.cd_serdes_2p5x.clk.attr.add("keep")
        platform.add_period_constraint(master_serdes.cd_serdes.clk, 16.0),
        platform.add_period_constraint(master_serdes.cd_serdes_10x.clk, 1.6),
        platform.add_period_constraint(master_serdes.cd_serdes_2p5x.clk, 6.4)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            master_serdes.cd_serdes.clk,
            master_serdes.cd_serdes_10x.clk,
            master_serdes.cd_serdes_2p5x.clk)

        counter = Signal(32)
        self.sync.master_serdes_serdes += counter.eq(counter + 1)
        self.comb += [
            master_serdes.encoder.d[0].eq(counter),
            master_serdes.encoder.d[1].eq(counter)
        ]

        master_sys_counter = Signal(32)
        self.sync.sys += master_sys_counter.eq(master_sys_counter + 1)
        self.comb += platform.request("user_led", 0).eq(master_sys_counter[26])

        master_serdes_counter = Signal(32)
        self.sync.master_serdes_serdes += master_serdes_counter.eq(master_serdes_counter + 1)
        self.comb += platform.request("user_led", 1).eq(master_serdes_counter[26])

        master_serdes_2p5x_counter = Signal(32)
        self.sync.master_serdes_serdes_2p5x += master_serdes_2p5x_counter.eq(master_serdes_2p5x_counter + 1)
        self.comb += platform.request("user_led", 2).eq(master_serdes_2p5x_counter[26])

        master_serdes_10x_counter = Signal(32)
        self.sync.master_serdes_serdes_10x += master_serdes_10x_counter.eq(master_serdes_10x_counter + 1)
        self.comb += platform.request("user_led", 3).eq(master_serdes_10x_counter[26])


        # slave

        slave_pll = SERDESPLL(125e6, 1.25e9)
        self.submodules += slave_pll

        slave_pads = platform.request("slave_serdes", 0)
        self.submodules.slave_serdes = slave_serdes = SERDES(
            slave_pll, slave_pads, mode="slave")
        if hasattr(slave_pads, "txen"):
            self.comb += slave_pads.txen.eq(1) # hdmi specific to enable link

        slave_serdes.cd_serdes.clk.attr.add("keep")
        slave_serdes.cd_serdes_10x.clk.attr.add("keep")
        slave_serdes.cd_serdes_2p5x.clk.attr.add("keep")
        platform.add_period_constraint(slave_serdes.cd_serdes.clk, 16.0),
        platform.add_period_constraint(slave_serdes.cd_serdes_10x.clk, 1.6),
        platform.add_period_constraint(slave_serdes.cd_serdes_2p5x.clk, 6.4)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            slave_serdes.cd_serdes.clk,
            slave_serdes.cd_serdes_10x.clk,
            slave_serdes.cd_serdes_2p5x.clk)

        counter = Signal(32)
        self.sync.slave_serdes_serdes += counter.eq(counter + 1)
        self.comb += [
            slave_serdes.encoder.d[0].eq(counter),
            slave_serdes.encoder.d[1].eq(counter)
        ]

        slave_sys_counter = Signal(32)
        self.sync.sys += slave_sys_counter.eq(slave_sys_counter + 1)
        self.comb += platform.request("user_led", 4).eq(slave_sys_counter[26])

        slave_serdes_counter = Signal(32)
        self.sync.slave_serdes_serdes += slave_serdes_counter.eq(slave_serdes_counter + 1)
        self.comb += platform.request("user_led", 5).eq(slave_serdes_counter[26])

        slave_serdes_2p5x_counter = Signal(32)
        self.sync.slave_serdes_serdes_2p5x += slave_serdes_2p5x_counter.eq(slave_serdes_2p5x_counter + 1)
        self.comb += platform.request("user_led", 6).eq(slave_serdes_2p5x_counter[26])

        slave_serdes_10x_counter = Signal(32)
        self.sync.slave_serdes_serdes_10x += slave_serdes_10x_counter.eq(slave_serdes_10x_counter + 1)
        self.comb += platform.request("user_led", 7).eq(slave_serdes_10x_counter[26])

        if analyzer == "master":
            analyzer_signals = [
                master_serdes.encoder.k[0],
                master_serdes.encoder.d[0],
                master_serdes.encoder.output[0],
                master_serdes.encoder.k[1],
                master_serdes.encoder.d[1],
                master_serdes.encoder.output[1],

                master_serdes.decoders[0].input,
                master_serdes.decoders[0].d,
                master_serdes.decoders[0].k,
                master_serdes.decoders[1].input,
                master_serdes.decoders[1].d,
                master_serdes.decoders[1].k
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 512, cd="master_serdes_serdes")

        if analyzer == "slave":
            analyzer_signals = [
                slave_serdes.encoder.k[0],
                slave_serdes.encoder.d[0],
                slave_serdes.encoder.output[0],
                slave_serdes.encoder.k[1],
                slave_serdes.encoder.d[1],
                slave_serdes.encoder.output[1],

                slave_serdes.decoders[0].input,
                slave_serdes.decoders[0].d,
                slave_serdes.decoders[0].k,
                slave_serdes.decoders[1].input,
                slave_serdes.decoders[1].d,
                slave_serdes.decoders[1].k
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 512, cd="slave_serdes_serdes")

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")


def main():
    platform = kcu105.Platform()
    platform.add_extension(serdes_io)
    if len(sys.argv) < 2:
        print("missing target (base or gth or multigth or serdes)")
        exit()
    if sys.argv[1] == "base":
        soc = BaseSoC(platform)
    elif sys.argv[1] == "gth":
        soc = GTHTestSoC(platform)
    elif sys.argv[1] == "multigth":
        soc = MultiGTHTestSoC(platform)
    elif sys.argv[1] == "serdes":
        soc = SERDESTestSoC(platform)
    builder = Builder(soc, output_dir="build_kcu105", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
