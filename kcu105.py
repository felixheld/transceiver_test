#!/usr/bin/env python3

from litex.gen import *
from litex.soc.interconnect.csr import *
from litex.build.generic_platform import *
from litex.boards.platforms import kcu105

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.gth_ultrascale import GTHChannelPLL, GTH
from transceiver.serdes_ultrascale import SERDESPLL, SERDES

from litescope import LiteScopeAnalyzer


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(1e9/platform.default_clk_period)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="KCU105 SERDES Test Design",
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

        gth.cd_rtio.clk.attr.add("keep")
        gth.cd_rtio_rx.clk.attr.add("keep")
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


serdes_io = [
    # cyusb3acc_005 fmc with loopback
    ("master_serdes", 0,
        Subsignal("clk_p", Pins("LPC:LA20_P")), # g21
        Subsignal("clk_n", Pins("LPC:LA20_N")), # g22
        Subsignal("tx_p", Pins("LPC:LA22_P")), # g24
        Subsignal("tx_n", Pins("LPC:LA22_N")), # g25
        Subsignal("rx_p", Pins("LPC:LA11_P")), # h16
        Subsignal("rx_n", Pins("LPC:LA11_N")), # h17
        IOStandard("LVDS"),
    ),

    ("slave_serdes", 0,
        Subsignal("clk_p", Pins("LPC:LA04_P")), # h10
        Subsignal("clk_n", Pins("LPC:LA04_P")), # h11
        Subsignal("tx_p", Pins("LPC:LA25_P")), # g27
        Subsignal("tx_n", Pins("LPC:LA25_N")), # g28
        Subsignal("rx_p", Pins("LPC:LA07_P")), # h13
        Subsignal("rx_n", Pins("LPC:LA07_N")), # h14
        IOStandard("LVDS"),
    ),
]


class SERDESControl(Module, AutoCSR):
    def __init__(self):
        self._tx_prbs_config = CSRStorage(2)

        self._rx_bitslip_value = CSRStorage(5)
        self._rx_delay_rst = CSR()
        self._rx_delay_inc = CSRStorage()
        self._rx_delay_ce = CSR()

        self._rx_prbs_config = CSRStorage(2)
        self._rx_prbs_errors = CSRStatus(32)

        # # #

        self.tx_prbs_config = self._tx_prbs_config.storage

        self.rx_bitslip_value = self._rx_bitslip_value.storage

        self.rx_prbs_config = self._rx_prbs_config.storage
        self.rx_prbs_errors = self._rx_prbs_errors.status


class SERDESTestSoC(BaseSoC):
    csr_map = {
        "master_serdes_control": 20,
        "slave_serdes_control": 21,
        "analyzer": 22
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform, analyzer="master"):
        BaseSoC.__init__(self, platform)

        # master

        master_pll = SERDESPLL(125e6, 1.25e9)
        self.comb += master_pll.refclk.eq(ClockSignal())
        self.submodules += master_pll

        master_pads = platform.request("master_serdes")
        self.submodules.master_serdes = master_serdes = SERDES(
            master_pll, master_pads, mode="master")
        self.comb += master_serdes.tx_produce_square_wave.eq(platform.request("user_dip_btn", 0))
        self.submodules.master_serdes_control = master_serdes_control = SERDESControl()
        self.comb += [
            master_serdes.tx_prbs_config.eq(master_serdes_control.tx_prbs_config),
            master_serdes.rx_bitslip_value.eq(master_serdes_control.rx_bitslip_value),
            master_serdes.rx_prbs_config.eq(master_serdes_control.rx_prbs_config),
            master_serdes_control.rx_prbs_errors.eq(master_serdes.rx_prbs_errors)
        ]

        master_serdes.cd_rtio.clk.attr.add("keep")
        master_serdes.cd_serdes.clk.attr.add("keep")
        master_serdes.cd_serdes_div.clk.attr.add("keep")
        platform.add_period_constraint(master_serdes.cd_rtio.clk, 16.0),
        platform.add_period_constraint(master_serdes.cd_serdes.clk, 1.6),
        platform.add_period_constraint(master_serdes.cd_serdes_div.clk, 6.4)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            master_serdes.cd_rtio.clk,
            master_serdes.cd_serdes.clk,
            master_serdes.cd_serdes_div.clk)

        counter = Signal(32)
        self.sync.master_serdes_rtio += counter.eq(counter + 1)
        self.comb += [
            master_serdes.encoder.d[0].eq(counter),
            master_serdes.encoder.d[1].eq(counter)
        ]

        master_sys_counter = Signal(32)
        self.sync.sys += master_sys_counter.eq(master_sys_counter + 1)
        self.comb += platform.request("user_led", 0).eq(master_sys_counter[26])

        master_rtio_counter = Signal(32)
        self.sync.master_serdes_rtio += master_rtio_counter.eq(master_rtio_counter + 1)
        self.comb += platform.request("user_led", 1).eq(master_rtio_counter[26])

        master_serdes_div_counter = Signal(32)
        self.sync.master_serdes_serdes_div += master_serdes_div_counter.eq(master_serdes_div_counter + 1)
        self.comb += platform.request("user_led", 2).eq(master_serdes_div_counter[26])

        master_serdes_counter = Signal(32)
        self.sync.master_serdes_serdes += master_serdes_counter.eq(master_serdes_counter + 1)
        self.comb += platform.request("user_led", 3).eq(master_serdes_counter[26])


        # slave

        slave_pll = SERDESPLL(125e6, 1.25e9)
        self.submodules += slave_pll

        slave_pads = platform.request("slave_serdes", 0)
        self.submodules.slave_serdes = slave_serdes = SERDES(
            slave_pll, slave_pads, mode="slave")
        self.comb += slave_serdes.tx_produce_square_wave.eq(platform.request("user_dip_btn", 1))
        if hasattr(slave_pads, "txen"):
            self.comb += slave_pads.txen.eq(1) # hdmi specific to enable link

        self.submodules.slave_serdes_control = slave_serdes_control = SERDESControl()
        self.comb += [
            slave_serdes.tx_prbs_config.eq(slave_serdes_control.tx_prbs_config),
            slave_serdes.rx_bitslip_value.eq(slave_serdes_control.rx_bitslip_value),
            slave_serdes.rx_prbs_config.eq(slave_serdes_control.rx_prbs_config),
            slave_serdes_control.rx_prbs_errors.eq(slave_serdes.rx_prbs_errors)
        ]

        slave_serdes.cd_rtio.clk.attr.add("keep")
        slave_serdes.cd_serdes.clk.attr.add("keep")
        slave_serdes.cd_serdes_div.clk.attr.add("keep")
        platform.add_period_constraint(slave_serdes.cd_rtio.clk, 16.0),
        platform.add_period_constraint(slave_serdes.cd_serdes.clk, 1.6),
        platform.add_period_constraint(slave_serdes.cd_serdes_div.clk, 6.4)
        self.platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            slave_serdes.cd_rtio.clk,
            slave_serdes.cd_serdes.clk,
            slave_serdes.cd_serdes_div.clk)

        counter = Signal(32)
        self.sync.slave_serdes_rtio += counter.eq(counter + 1)
        self.comb += [
            slave_serdes.encoder.d[0].eq(counter),
            slave_serdes.encoder.d[1].eq(counter)
        ]

        slave_sys_counter = Signal(32)
        self.sync.sys += slave_sys_counter.eq(slave_sys_counter + 1)
        self.comb += platform.request("user_led", 4).eq(slave_sys_counter[26])

        slave_rtio_counter = Signal(32)
        self.sync.slave_serdes_rtio += slave_rtio_counter.eq(slave_rtio_counter + 1)
        self.comb += platform.request("user_led", 5).eq(slave_rtio_counter[26])

        slave_serdes_div_counter = Signal(32)
        self.sync.slave_serdes_serdes_div += slave_serdes_div_counter.eq(slave_serdes_div_counter + 1)
        self.comb += platform.request("user_led", 6).eq(slave_serdes_div_counter[26])

        slave_serdes_counter = Signal(32)
        self.sync.slave_serdes_serdes += slave_serdes_counter.eq(slave_serdes_counter + 1)
        self.comb += platform.request("user_led", 7).eq(slave_serdes_counter[26])

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
                master_serdes.decoders[1].k,

                master_serdes.rx_prbs_errors,
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 512, cd="master_serdes_rtio")

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
                slave_serdes.decoders[1].k,

                slave_serdes.rx_prbs_errors,
            ]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 512, cd="slave_serdes_rtio")

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")


def main():
    platform = kcu105.Platform()
    platform.add_extension(serdes_io)
    soc = GTHTestSoC(platform)
    #soc = SERDESTestSoC(platform)
    builder = Builder(soc, output_dir="build_kcu105", csr_csv="test/csr.csv")
    builder.build()


if __name__ == "__main__":
    main()
