from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from transceiver.line_coding import Encoder


class SERDESPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate):
        assert refclk_freq == 125e6
        assert linerate == 1.25e9
        self.lock = Signal()
        self.rtio_clk = Signal()
        self.serdes_clk = Signal()
        self.serdes_div_clk = Signal()
        # refclk: 125MHz
        # pll vco: 1250MHz
        # rtio: 62.5MHz
        # serdes = 625MHz
        # serdes_div = 156.25MHz
        pll_locked = Signal()
        pll_fb = Signal()
        pll_rtio_clk = Signal()
        pll_serdes_clk = Signal()
        pll_serdes_div_clk = Signal()
        self.specials += [
            Instance("PLLE2_BASE",
                p_STARTUP_WAIT="FALSE", o_LOCKED=pll_locked,

                # VCO @ 1.25GHz
                p_REF_JITTER1=0.01, p_CLKIN1_PERIOD=8.0,
                p_CLKFBOUT_MULT=4, p_DIVCLK_DIVIDE=1,
                i_CLKIN1=refclk, i_CLKFBIN=pll_fb,
                o_CLKFBOUT=pll_fb,

                # 62.5MHz: rtio
                p_CLKOUT0_DIVIDE=20, p_CLKOUT0_PHASE=0.0,
                o_CLKOUT0=pll_rtio_clk,

                # 625MHz: serdes
                p_CLKOUT1_DIVIDE=2, p_CLKOUT1_PHASE=0.0,
                o_CLKOUT1=pll_serdes_clk,

                # 625MHz: serdes_div
                p_CLKOUT1_DIVIDE=8, p_CLKOUT1_PHASE=0.0,
                o_CLKOUT1=pll_serdes_div_clk,
            ),
            Instance("BUFG", i_I=pll_rtio_clk, o_O=self.rtio_clk),
            Instance("BUFG", i_I=pll_serdes_clk, o_O=self.serdes_clk),
            Instance("BUFG", i_I=pll_serdes_div_clk, o_O=self.serdes_div_clk)
        ]


class SERDESTXGearbox(Module):
    def __init__(self):
        self.i = Signal(20)
        self.o = Signal(8)

        # # #

        # TODO
        # 20 bits (rtio) to 8 bits (serdes_div) gearbox


class SERDES(Module):
    def __init__(self, pll, tx_pads):
        self.produce_square_wave = Signal()
        self.submodules.encoder = ClockDomainsRenamer("rtio")(
            Encoder(2, True))

        self.submodules.tx_gearbox = SERDESTXGearbox()
        self.comb += \
            If(self.produce_square_wave,
                # square wave @ linerate/20 for scope observation
                self.tx_gearbox.i.eq(0b11111111110000000000)
            ).Else(
                self.tx_gearbox.i.eq(Cat(self.encoder.output[0],
                                         self.encoder.output[1]))
            )

        serdes_o = Signal()
        self.specials += [
            Instance("OSERDESE3",
                p_DATA_WIDTH=8, p_INIT=0,
                p_IS_CLK_INVERTED=0, p_IS_CLKDIV_INVERTED=0, p_IS_RST_INVERTED=0,

                o_OQ=serdes_o,
                i_RST=ResetSignal(),
                i_CLK=ClockSignal("serdes"), i_CLKDIV=ClockSignal("serdes_div"),
                i_D1=self.tx_gearbox.o[0], i_D2=self.tx_gearbox.o[1],
                i_D3=self.tx_gearbox.o[2], i_D4=self.tx_gearbox.o[3],
                i_D5=self.tx_gearbox.o[4], i_D6=self.tx_gearbox.o[5],
                i_D7=self.tx_gearbox.o[6], i_D8=self.tx_gearbox.o[7]
            )
            Instance("OBUFDS",
                i_I=serdes_o,
                o_O=tx_pads.p,
                o_OB=tx_pads.n
            )
        ]

        self.clock_domains.cd_rtio = ClockDomain()
        self.clock_domains.cd_serdes = ClockDomain()
        self.clock_domains.cd_serdes_div = ClockDomain()
        self.comb += [
            self.cd_rtio.clk.eq(pll.rtio_clk),
        ]
        self.specials += [
            AsyncResetSynchronizer(self.cd_rtio, ResetSignal()),       # FIXME
            AsyncResetSynchronizer(self.cd_serdes, ResetSignal()),     # FIXME
            AsyncResetSynchronizer(self.cd_serdes_div, ResetSignal()), # FIXME
        ]
