from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from transceiver.line_coding import Encoder, Decoder
from transceiver.gearbox import Gearbox
from transceiver.bitslip import BitSlip


class SERDESPLL(Module):
    def __init__(self, refclk_freq, linerate):
        assert refclk_freq == 125e6
        assert linerate == 1.25e9
        self.lock = Signal()
        self.refclk = Signal()
        self.rtio_clk = Signal()
        self.serdes_clk = Signal()
        self.serdes_div_clk = Signal()

        # refclk: 125MHz
        # pll vco: 1250MHz
        # rtio: 62.5MHz
        # serdes = 625MHz
        # serdes_div = 156.25MHz
        self.linerate = linerate

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
                p_CLKFBOUT_MULT=10, p_DIVCLK_DIVIDE=1,
                i_CLKIN1=self.refclk, i_CLKFBIN=pll_fb,
                o_CLKFBOUT=pll_fb,

                # 62.5MHz: rtio
                p_CLKOUT0_DIVIDE=20, p_CLKOUT0_PHASE=0.0,
                o_CLKOUT0=pll_rtio_clk,

                # 625MHz: serdes
                p_CLKOUT1_DIVIDE=2, p_CLKOUT1_PHASE=0.0,
                o_CLKOUT1=pll_serdes_clk,

                # 156.25MHz: serdes_div
                p_CLKOUT2_DIVIDE=8, p_CLKOUT2_PHASE=0.0,
                o_CLKOUT2=pll_serdes_div_clk
            ),
            Instance("BUFG", i_I=pll_rtio_clk, o_O=self.rtio_clk),
            Instance("BUFG", i_I=pll_serdes_clk, o_O=self.serdes_clk),
            Instance("BUFG", i_I=pll_serdes_div_clk, o_O=self.serdes_div_clk)
        ]
        self.comb += self.lock.eq(pll_locked)


class PhaseDetector(Module):
    # TODO: test and handle corner cases
    def __init__(self):
        self.mdata = Signal(8)
        self.sdata = Signal(8)

        self.ce = Signal()
        self.inc = Signal()

        # # #

        # algorithm:
        # - if the two samples taken a half-bit period apart (following a transition)
        #   are the same, then the sampling point is too late and the input delays
        #   need to be reduced by one tap
        # - if the two samples taken (following a transition) are different, then
        #   the sampling point is too early and the input delays need to be increased
        #   by one tap

        mdata_d = Signal(8)
        sdata_d = Signal(8)
        self.sync += [
            mdata_d.eq(self.mdata),
            sdata_d.eq(self.sdata)
        ]

        transition = Signal()
        self.comb += transition.eq((mdata_d != self.mdata) & (sdata_d != self.sdata))

        self.sync += [
            self.ce.eq(0),
            self.inc.eq(0),
            If(transition,
                self.ce.eq(1),
                If(self.mdata == self.sdata,
                    self.inc.eq(0)
                ).Else(
                    self.inc.eq(1)
                )
            )
        ]


class SERDES(Module):
    def __init__(self, pll, clock_pads, tx_pads, rx_pads, mode="master"):
        self.produce_square_wave = Signal()
        self.submodules.encoder = ClockDomainsRenamer("rtio")(
            Encoder(2, True))
        self.decoders = [ClockDomainsRenamer("rtio")(
            Decoder(True)) for _ in range(2)]
        self.submodules += self.decoders

        # clocking
        # master mode:
        # - linerate/10 pll refclk provided externally
        # - linerate/10 clock generated on clock_pads
        # slave mode:
        # - linerate/10 pll refclk provided by clock pads
        self.clock_domains.cd_rtio = ClockDomain()
        self.clock_domains.cd_serdes = ClockDomain()
        self.clock_domains.cd_serdes_div = ClockDomain()
        self.comb += [
            self.cd_rtio.clk.eq(pll.rtio_clk),
            self.cd_serdes.clk.eq(pll.serdes_clk),
            self.cd_serdes_div.clk.eq(pll.serdes_div_clk)
        ]
        self.specials += [
            AsyncResetSynchronizer(self.cd_rtio, ~pll.lock),
            AsyncResetSynchronizer(self.cd_serdes, ~pll.lock),
            AsyncResetSynchronizer(self.cd_serdes_div, ~pll.lock)
        ]

        # tx clock
        if mode == "master":
            self.submodules.tx_clk_gearbox = Gearbox(20, "rtio", 8, "serdes_div")
            self.comb += self.tx_clk_gearbox.i.eq(0b11111000001111100000) # linerate/10

            clk_o = Signal()
            self.specials += [
                Instance("OSERDESE3",
                    p_DATA_WIDTH=8, p_INIT=0,
                    p_IS_CLK_INVERTED=0, p_IS_CLKDIV_INVERTED=0, p_IS_RST_INVERTED=0,

                    o_OQ=clk_o,
                    i_RST=ResetSignal("serdes_div"),
                    i_CLK=ClockSignal("serdes"), i_CLKDIV=ClockSignal("serdes_div"),
                    i_D=self.tx_clk_gearbox.o
                ),
                Instance("OBUFDS",
                    i_I=clk_o,
                    o_O=clock_pads.p,
                    o_OB=clock_pads.n
                )
            ]

        # tx
        self.submodules.tx_gearbox = Gearbox(20, "rtio", 8, "serdes_div")
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
                i_RST=ResetSignal("serdes_div"),
                i_CLK=ClockSignal("serdes"), i_CLKDIV=ClockSignal("serdes_div"),
                i_D=self.tx_gearbox.o
            ),
            Instance("OBUFDS",
                i_I=serdes_o,
                o_O=tx_pads.p,
                o_OB=tx_pads.n
            )
        ]

        # rx clock
        if mode == "slave":
            clk_i = Signal()
            self.specials += [
                Instance("IBUFDS",
                    i_I=clock_pads.p,
                    i_IB=clock_pads.n,
                    o_O=clk_i
                )
            ]
            self.comb += pll.refclk.eq(clk_i)

        # rx
        self.submodules.rx_gearbox = Gearbox(8, "serdes_div", 20, "rtio")
        self.submodules.rx_bitslip = ClockDomainsRenamer("rtio")(BitSlip(20))

        self.submodules.phase_detector = ClockDomainsRenamer("serdes_div")(PhaseDetector())

        # use 2 serdes for phase detection: 1 master/ 1 slave
        serdes_m_i_nodelay = Signal()
        serdes_s_i_nodelay = Signal()
        self.specials += [
            Instance("IBUFDS_DIFF_OUT",
                i_I=rx_pads.p,
                i_IB=rx_pads.n,
                o_O=serdes_m_i_nodelay,
                o_OB=serdes_s_i_nodelay,
            )
        ]

        serdes_m_i_delayed = Signal()
        serdes_m_q = Signal(8)
        # FIXME: idelay taps works differently on ultrascale (2.5ps to 15ps for a tap...)
        serdes_m_delay_value = int(1/(2*pll.linerate)/15e-12) # half bit period
        assert serdes_m_delay_value < 512
        self.specials += [
            Instance("IDELAYE3",
                p_CASCADE="NONE", p_UPDATE_MODE="ASYNC",p_REFCLK_FREQUENCY=200.0,
                p_IS_CLK_INVERTED=0, p_IS_RST_INVERTED=0,
                p_DELAY_FORMAT="COUNT", p_DELAY_SRC="IDATAIN",
                p_DELAY_TYPE="VARIABLE", p_DELAY_VALUE=serdes_m_delay_value,

                i_CLK=ClockSignal("serdes_div"),
                i_RST=ResetSignal("serdes_div"),
                # For now desactivate for simulation
                #i_INC=self.phase_detector.inc, i_EN_VTC=0,
                #i_CE=self.phase_detector.ce,
                i_INC=1, i_EN_VTC=0,
                i_CE=0,

                i_IDATAIN=serdes_m_i_nodelay, o_DATAOUT=serdes_m_i_delayed
            ),
            Instance("ISERDESE3",
                p_DATA_WIDTH=8,

                i_D=serdes_m_i_delayed,
                i_RST=ResetSignal("serdes_div"),
                i_FIFO_RD_CLK=0, i_FIFO_RD_EN=0,
                i_CLK=ClockSignal("serdes"), i_CLK_B=~ClockSignal("serdes"),
                i_CLKDIV=ClockSignal("serdes_div"),
                o_Q=serdes_m_q
            ),
        ]
        self.comb += self.phase_detector.mdata.eq(serdes_m_q)

        serdes_s_i_delayed = Signal()
        serdes_s_q = Signal(8)
        # FIXME: idelay taps works differently on ultrascale (2.5ps to 15ps for a tap...)
        serdes_s_idelay_value = int(1/(pll.linerate)/15e-12) # bit period
        assert serdes_s_idelay_value < 512
        self.specials += [
            Instance("IDELAYE3",
                p_CASCADE="NONE", p_UPDATE_MODE="ASYNC",p_REFCLK_FREQUENCY=200.0,
                p_IS_CLK_INVERTED=0, p_IS_RST_INVERTED=0,
                p_DELAY_FORMAT="COUNT", p_DELAY_SRC="IDATAIN",
                p_DELAY_TYPE="VARIABLE", p_DELAY_VALUE=serdes_s_idelay_value,

                i_CLK=ClockSignal("serdes_div"),
                i_RST=ResetSignal("serdes_div"),
                # For now desactivate for simulation
                #i_INC=self.phase_detector.inc, i_EN_VTC=0,
                #i_CE=self.phase_detector.ce,
                i_INC=1, i_EN_VTC=0,
                i_CE=0,

                i_IDATAIN=~serdes_s_i_nodelay, o_DATAOUT=serdes_s_i_delayed
            ),
            Instance("ISERDESE3",
                p_DATA_WIDTH=8,

                i_D=serdes_s_i_delayed,
                i_RST=ResetSignal("serdes_div"),
                i_FIFO_RD_CLK=0, i_FIFO_RD_EN=0,
                i_CLK=ClockSignal("serdes"), i_CLK_B=~ClockSignal("serdes"),
                i_CLKDIV=ClockSignal("serdes_div"),
                o_Q=serdes_s_q
            ),
        ]
        self.comb += self.phase_detector.sdata.eq(serdes_s_q)

        self.comb += [
            self.rx_gearbox.i.eq(serdes_m_q),
            self.rx_bitslip.value.eq(0), # FIXME
            self.rx_bitslip.i.eq(self.rx_gearbox.o),
            self.decoders[0].input.eq(self.rx_bitslip.o[:10]),
            self.decoders[1].input.eq(self.rx_bitslip.o[10:])
        ]
