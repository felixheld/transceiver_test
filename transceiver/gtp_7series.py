from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from transceiver.line_coding import Encoder, Decoder
from transceiver.gtp_7series_init import GTPInit
from transceiver.clock_aligner import BruteforceClockAligner


class GTPQuadPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate):
        self.clk = Signal()
        self.refclk = Signal()
        self.reset = Signal()
        self.lock = Signal()
        self.config = self.compute_config(refclk_freq, linerate)

        # # #

        self.specials += \
            Instance("GTPE2_COMMON",
                # common
                #i_GTREFCLK0=refclk, # FIXME
                i_GTGREFCLK0=refclk, # FIXME
                i_BGBYPASSB=1,
                i_BGMONITORENB=1,
                i_BGPDB=1,
                i_BGRCALOVRD=0b11111,
                i_RCALENB=1,

                # pll0
                #p_PLL0_CFG=,
                #p_PLL0_LOCK_CFG=,
                #p_PLL0_INIT_CFG=,
                p_PLL0_FBDIV=self.config["n2"],
                p_PLL0_FBDIV_45=self.config["n1"],
                p_PLL0_REFCLK_DIV=self.config["m"],
                i_PLL0LOCKEN=1,
                i_PLL0PD=0,
                i_PLL0REFCLKSEL=0b111,
                i_PLL0RESET=self.reset,
                o_PLL0LOCK=self.lock,
                o_PLL0OUTCLK=self.clk,
                o_PLL0OUTREFCLK=self.refclk,

                # pll1 (not used)
                i_PLL1PD=1,
             )

    @staticmethod
    def compute_config(refclk_freq, linerate):
        for n1 in 4, 5:
            for n2 in 1, 2, 3, 4, 5:
                for m in 1, 2:
                    vco_freq = refclk_freq*(n1*n2)/m
                    if 1.6e9 <= vco_freq <= 3.3e9:
                        for d in 1, 2, 4, 8, 16:
                            current_linerate = vco_freq*2/d
                            if current_linerate == linerate:
                                return {"n1": n1, "n2": n2, "m": m, "d": d,
                                        "vco_freq": vco_freq,
                                        "clkin": refclk_freq,
                                        "linerate": linerate}
        msg = "No config found for {:3.2f} MHz refclk / {:3.2f} Gbps linerate."
        raise ValueError(msg.format(refclk_freq/1e6, linerate/1e9))

    def __repr__(self):
        r = """
GTPQuadPLL
==============
  overview:
  ---------
       +--------------------------------------------------+
       |                                                  |
       |   +-----+  +---------------------------+ +-----+ |
       |   |     |  | Phase Frequency Detector  | |     | |
CLKIN +----> /M  +-->       Charge Pump         +-> VCO +---> CLKOUT
       |   |     |  |       Loop Filter         | |     | |
       |   +-----+  +---------------------------+ +--+--+ |
       |              ^                              |    |
       |              |    +-------+    +-------+    |    |
       |              +----+  /N2  <----+  /N1  <----+    |
       |                   +-------+    +-------+         |
       +--------------------------------------------------+
                            +-------+
                   CLKOUT +->  2/D  +-> LINERATE
                            +-------+
  config:
  -------
    CLKIN    = {clkin}MHz
    CLKOUT   = CLKIN x (N1 x N2) / M = {clkin}MHz x ({n1} x {n2}) / {m}
             = {vco_freq}GHz
    LINERATE = CLKOUT x 2 / D = {vco_freq}GHz x 2 / {d}
             = {linerate}GHz
""".format(clkin=self.config["clkin"]/1e6,
           n1=self.config["n1"],
           n2=self.config["n2"],
           m=self.config["m"],
           vco_freq=self.config["vco_freq"]/1e9,
           d=self.config["d"],
           linerate=self.config["linerate"]/1e9)
        return r


class GTP(Module):
    def __init__(self, qpll, tx_pads, rx_pads, sys_clk_freq,
                 clock_aligner=True, internal_loopback=False,
                 tx_polarity=0, rx_polarity=0):
        self.produce_square_wave = Signal()
        self.submodules.encoder = ClockDomainsRenamer("rtio")(
            Encoder(2, True))
        self.decoders = [ClockDomainsRenamer("rtio_rx")(
            Decoder(True)) for _ in range(2)]
        self.submodules += self.decoders

        self.rx_ready = Signal()

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        self.rtio_clk_freq = qpll.config["linerate"]/20

        # # #

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTPInit(sys_clk_freq, False)
        # RX receives restart commands from RTIO domain
        rx_init = ClockDomainsRenamer("rtio")(
            GTPInit(self.rtio_clk_freq, True))
        self.submodules += tx_init, rx_init
        self.comb += [
            tx_init.plllock.eq(qpll.lock),
            rx_init.plllock.eq(qpll.lock),
            qpll.reset.eq(tx_init.pllreset)
        ]

        assert qpll.config["linerate"] < 6.6e9
        rxcdr_cfgs = {
            1 : 0x0000107FE406001041010,
            2 : 0x0000107FE206001041010,
            4 : 0x0000107FE106001041010,
            8 : 0x0000107FE086001041010
        }

        txdata = Signal(20)
        rxdata = Signal(20)
        self.specials += \
            Instance("GTPE2_CHANNEL",
                i_GTRESETSEL=0,
                i_RESETOVRD=0,

                # PMA Attributes
                p_PMA_RSV=0x00018480,
                p_PMA_RSV2=0x2050,
                p_PMA_RSV3=0,
                p_PMA_RSV4=0,
                p_RX_BIAS_CFG=0b100,
                p_RX_CM_TRIM=0b010,
                p_RX_OS_CFG=0b10000000,
                p_RX_CLK25_DIV=5,
                p_TX_CLK25_DIV=5,

                # Power-Down Attributes
                p_PD_TRANS_TIME_FROM_P2=0x3c,
                p_PD_TRANS_TIME_NONE_P2=0x3c,
                p_PD_TRANS_TIME_TO_P2=0x64,

                # QPLL
                i_PLL0CLK=qpll.clk,
                i_PLL0REFCLK=qpll.refclk,

                # TX clock
                p_TXBUF_EN="FALSE",
                p_TX_XCLK_SEL="TXUSR",
                o_TXOUTCLK=self.txoutclk,
                p_TXOUT_DIV=qpll.config["d"],
                i_TXSYSCLKSEL=0b00,
                i_TXOUTCLKSEL=0b11,

                # TX Startup/Reset
                i_GTTXRESET=tx_init.gtXxreset,
                o_TXRESETDONE=tx_init.Xxresetdone,
                p_TXSYNC_OVRD=1,
                i_TXDLYSRESET=tx_init.Xxdlysreset,
                o_TXDLYSRESETDONE=tx_init.Xxdlysresetdone,
                i_TXPHINIT=tx_init.Xxphinit,
                o_TXPHINITDONE=tx_init.Xxphinitdone,
                i_TXPHALIGNEN=1,
                i_TXPHALIGN=tx_init.Xxphalign,
                o_TXPHALIGNDONE=tx_init.Xxphaligndone,
                i_TXDLYEN=tx_init.Xxdlyen,
                i_TXUSERRDY=tx_init.Xxuserrdy,

                # TX data
                p_TX_DATA_WIDTH=20,
                i_TXCHARDISPMODE=Cat(txdata[9], txdata[19]),
                i_TXCHARDISPVAL=Cat(txdata[8], txdata[18]),
                i_TXDATA=Cat(txdata[:8], txdata[10:18]),
                i_TXUSRCLK=ClockSignal("rtio"),
                i_TXUSRCLK2=ClockSignal("rtio"),

                # TX electrical
                i_TXBUFDIFFCTRL=0b100,
                i_TXDIFFCTRL=0b1000,

                # Internal Loopback
                i_LOOPBACK=0b010 if internal_loopback else 0b000,

                # RX Startup/Reset
                i_GTRXRESET=rx_init.gtXxreset,
                o_RXRESETDONE=rx_init.Xxresetdone,
                i_RXDLYSRESET=rx_init.Xxdlysreset,
                o_RXDLYSRESETDONE=rx_init.Xxdlysresetdone,
                o_RXPHALIGNDONE=rx_init.Xxphaligndone,
                i_RXSYNCALLIN=rx_init.Xxphaligndone,
                i_RXUSERRDY=rx_init.Xxuserrdy,
                i_RXSYNCIN=0,
                i_RXSYNCMODE=1,
                o_RXSYNCDONE=rx_init.Xxsyncdone,

                # RX clock
                p_RXBUF_EN="FALSE",
                p_RX_XCLK_SEL="RXUSR",
                p_RXOUT_DIV=qpll.config["d"],
                i_RXDDIEN=1,
                i_RXSYSCLKSEL=0b00,
                i_RXOUTCLKSEL=0b010,
                o_RXOUTCLK=self.rxoutclk,
                i_RXUSRCLK=ClockSignal("rtio_rx"),
                i_RXUSRCLK2=ClockSignal("rtio_rx"),
                p_RXCDR_CFG=rxcdr_cfgs[qpll.config["d"]],

                # RX Clock Correction Attributes
                p_CLK_CORRECT_USE="FALSE",
                p_CLK_COR_SEQ_1_1=0b0100000000,
                p_CLK_COR_SEQ_2_1=0b0100000000,
                p_CLK_COR_SEQ_1_ENABLE=0b1111,
                p_CLK_COR_SEQ_2_ENABLE=0b1111,

                # RX data
                p_RX_DATA_WIDTH=20,
                o_RXDISPERR=Cat(rxdata[9], rxdata[19]),
                o_RXCHARISK=Cat(rxdata[8], rxdata[18]),
                o_RXDATA=Cat(rxdata[:8], rxdata[10:18]),

                # Polarity
                i_TXPOLARITY=tx_polarity,
                i_RXPOLARITY=rx_polarity,

                # Pads
                i_GTPRXP=rx_pads.p,
                i_GTPRXN=rx_pads.n,
                o_GTPTXP=tx_pads.p,
                o_GTPTXN=tx_pads.n,
            )

        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_rtio = ClockDomain()
        txoutclk_bufg = Signal()
        txoutclk_bufr = Signal()
        tx_bufr_div = qpll.config["clkin"]/self.rtio_clk_freq
        assert tx_bufr_div == int(tx_bufr_div)
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=txoutclk_bufg),
            # TODO: use MMCM instead?
            Instance("BUFR", i_I=txoutclk_bufg, o_O=txoutclk_bufr,
                i_CE=1, p_BUFR_DIVIDE=str(int(tx_bufr_div))),
            Instance("BUFG", i_I=txoutclk_bufr, o_O=self.cd_rtio.clk),
            AsyncResetSynchronizer(self.cd_rtio, tx_reset_deglitched)
        ]
        rx_reset_deglitched = Signal()
        rx_reset_deglitched.attr.add("no_retiming")
        self.sync.rtio += rx_reset_deglitched.eq(~rx_init.done)
        self.clock_domains.cd_rtio_rx = ClockDomain()
        self.specials += [
            Instance("BUFG", i_I=self.rxoutclk, o_O=self.cd_rtio_rx.clk),
            AsyncResetSynchronizer(self.cd_rtio_rx, rx_reset_deglitched)
        ]

        self.comb += [
             If(self.produce_square_wave,
                # square wave @ linerate/20 for scope observation
                txdata.eq(0b11111111110000000000)
            ).Else(
                txdata.eq(Cat(self.encoder.output[0],
                              self.encoder.output[1]))
            ),
            self.decoders[0].input.eq(rxdata[:10]),
            self.decoders[1].input.eq(rxdata[10:])
        ]

        if clock_aligner:
            clock_aligner = BruteforceClockAligner(0b0101111100, self.rtio_clk_freq)
            self.submodules += clock_aligner
            self.comb += [
                clock_aligner.rxdata.eq(rxdata),
                rx_init.restart.eq(clock_aligner.restart),
                self.rx_ready.eq(clock_aligner.ready)
            ]
        else:
            self.comb += self.rx_ready.eq(rx_init.done)
