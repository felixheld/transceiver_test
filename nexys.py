#!/usr/bin/env python3

from litex.gen import *
from litex.soc.interconnect.csr import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer
from litex.build.generic_platform import *
from litex.boards.platforms import nexys_video as nexys

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from transceiver.serdes_7series import SERDESPLL, SERDES

from litescope import LiteScopeAnalyzer


serdes_io = [
    # hdmi loopback
    ("master_serdes", 0,
        Subsignal("clk_p", Pins("T1"), IOStandard("TMDS_33")), # hdmi_out clk
        Subsignal("clk_n", Pins("U1"), IOStandard("TMDS_33")), # hdmi_out clk
        Subsignal("tx_p", Pins("W1"), IOStandard("TMDS_33")),  # hdmi_out data0
        Subsignal("tx_n", Pins("Y1"), IOStandard("TMDS_33")),  # hdmi_out data0
        Subsignal("rx_p", Pins("W2"), IOStandard("TMDS_33")),  # hdmi_in data1
        Subsignal("rx_n", Pins("Y2"), IOStandard("TMDS_33")),  # hdmi_in data1
    ),

    ("slave_serdes", 0,
        Subsignal("clk_p", Pins("V4"), IOStandard("TMDS_33")), # hdmi_in clk
        Subsignal("clk_n", Pins("W4"), IOStandard("TMDS_33")), # hdmi_in clk
        Subsignal("tx_p", Pins("AA1"), IOStandard("TMDS_33")), # hdmi_out data1
        Subsignal("tx_n", Pins("AB1"), IOStandard("TMDS_33")), # hdmi_out data1
        Subsignal("rx_p", Pins("Y3"), IOStandard("TMDS_33")),  # hdmi_in data0
        Subsignal("rx_n", Pins("AA3"), IOStandard("TMDS_33")), # hdmi_in data0
        Subsignal("txen", Pins("R3"), IOStandard("LVCMOS33")),
    ),
    # cyusb3acc_005 fmc with loopback
    ("master_serdes", 1,
        Subsignal("clk_p", Pins("LPC:LA20_P")), # g21
        Subsignal("clk_n", Pins("LPC:LA20_N")), # g22
        Subsignal("tx_p", Pins("LPC:LA22_P")), # g24
        Subsignal("tx_n", Pins("LPC:LA22_N")), # g25
        Subsignal("rx_p", Pins("LPC:LA11_P")), # h16
        Subsignal("rx_n", Pins("LPC:LA11_N")), # h17
        IOStandard("LVDS_25"),
    ),

    ("slave_serdes", 1,
        Subsignal("clk_p", Pins("LPC:LA04_P")), # h10
        Subsignal("clk_n", Pins("LPC:LA04_P")), # h11
        Subsignal("tx_p", Pins("LPC:LA25_P")), # g27
        Subsignal("tx_n", Pins("LPC:LA25_N")), # g28
        Subsignal("rx_p", Pins("LPC:LA07_P")), # h13
        Subsignal("rx_n", Pins("LPC:LA07_N")), # h14
        IOStandard("LVDS_25"),
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
            ident="Nexys SERDES Test Design",
            with_timer=False
        )
        self.submodules.crg = _CRG(platform)
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        self.crg.cd_sys.clk.attr.add("keep")
        platform.add_period_constraint(self.crg.cd_sys.clk, 10.0),


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
        self.rx_delay_rst = self._rx_delay_rst.r & self._rx_delay_rst.re
        self.rx_delay_inc = self._rx_delay_inc.storage
        self.rx_delay_ce = self._rx_delay_ce.r & self._rx_delay_ce.re

        self.rx_prbs_config = self._rx_prbs_config.storage
        self.rx_prbs_errors = self._rx_prbs_errors.status


class SERDESTestSoC(BaseSoC):
    csr_map = {
        "master_serdes_control": 20,
        "slave_serdes_control": 21,
        "analyzer": 22
    }
    csr_map.update(BaseSoC.csr_map)
    def __init__(self, platform, medium="fmc", analyzer="master"):
        BaseSoC.__init__(self, platform)

        # master

        master_pll = SERDESPLL(125e6, 1.25e9)
        self.comb += master_pll.refclk.eq(self.crg.cd_clk125.clk)
        self.submodules += master_pll

        if medium == "hdmi":
            master_pads = platform.request("master_serdes", 0)
        elif medium == "fmc":
            master_pads = platform.request("master_serdes", 1)
        self.submodules.master_serdes = master_serdes = SERDES(
            master_pll, master_pads, mode="master")
        self.comb += master_serdes.tx_produce_square_wave.eq(platform.request("user_sw", 0))
        self.submodules.master_serdes_control = master_serdes_control = SERDESControl()
        self.comb += [
            master_serdes.tx_prbs_config.eq(master_serdes_control.tx_prbs_config),
            master_serdes.rx_bitslip_value.eq(master_serdes_control.rx_bitslip_value),
            master_serdes.rx_delay_rst.eq(master_serdes_control.rx_delay_rst),
            master_serdes.rx_delay_inc.eq(master_serdes_control.rx_delay_inc),
            master_serdes.rx_delay_ce.eq(master_serdes_control.rx_delay_ce),
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
        self.comb += slave_pll.refclk.eq(self.crg.cd_clk125.clk)
        self.submodules += slave_pll

        if medium == "hdmi":
            slave_pads = platform.request("slave_serdes", 0)
        elif medium == "fmc":
            slave_pads = platform.request("slave_serdes", 1)
        self.submodules.slave_serdes = slave_serdes = SERDES(
            slave_pll, slave_pads, mode="slave")
        self.comb += slave_serdes.tx_produce_square_wave.eq(platform.request("user_sw", 1))
        if hasattr(slave_pads, "txen"):
            self.comb += slave_pads.txen.eq(1) # hdmi specific to enable link

        self.submodules.slave_serdes_control = slave_serdes_control = SERDESControl()
        self.comb += [
            slave_serdes.tx_prbs_config.eq(slave_serdes_control.tx_prbs_config),
            slave_serdes.rx_bitslip_value.eq(slave_serdes_control.rx_bitslip_value),
            slave_serdes.rx_delay_rst.eq(slave_serdes_control.rx_delay_rst),
            slave_serdes.rx_delay_inc.eq(slave_serdes_control.rx_delay_inc),
            slave_serdes.rx_delay_ce.eq(slave_serdes_control.rx_delay_ce),
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


        # fmc
        if medium == "fmc":
            platform.add_platform_command("set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets slave_serdes_clk_i")

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")


def main():
    platform = nexys.Platform()
    platform.add_extension(serdes_io)
    soc = SERDESTestSoC(platform)
    builder = Builder(soc, output_dir="build_nexys", csr_csv="test/csr.csv")
    vns = builder.build()
    soc.do_exit(vns)


if __name__ == "__main__":
    main()
