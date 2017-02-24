from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from transceiver.line_coding import Encoder
from transceiver.gtp_7series_init import GTPInit


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
                i_GTREFCLK0=refclk,
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
                i_PLL0REFCLKSEL=0b001,
                i_PLL0RESET=self.reset,
                o_PLL0LOCK=self.lock,
                o_PLL0OUTCLK=self.clk,
                o_PLL0OUTREFCLK=self.refclk,

                # pll1 (not used)
                i_PLL1PD=1,
             )

    @classmethod
    def compute_config(self, refclk_freq, linerate):
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
    def __init__(self, qpll, tx_pads, sys_clk_freq,
                 tx_polarity=0):
        self.submodules.encoder = ClockDomainsRenamer("rtio")(
            Encoder(2, True))

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()

        self.rtio_clk_freq = qpll.config["linerate"]/20

        # # #

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTPInit(sys_clk_freq, False)
        self.submodules += tx_init
        self.comb += [
            tx_init.plllock.eq(qpll.lock),
            qpll.reset.eq(tx_init.pllreset)
        ]

        txdata = Signal(20)
        self.specials += \
            Instance("GTPE2_CHANNEL",
                i_GTRESETSEL=0,
                i_RESETOVRD=0,

                # PMA Attributes
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
                i_TXSYSCLKSEL=0b00,
                i_TXOUTCLKSEL=0b11,

                # TX Startup/Reset
                i_GTTXRESET=tx_init.gtXxreset,
                o_TXRESETDONE=tx_init.Xxresetdone,
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

                # disable RX
                i_RXPD=0b11,

                # Polarity
                i_TXPOLARITY=tx_polarity,

                # Pads
                o_GTPTXP=tx_pads.p,
                o_GTPTXN=tx_pads.n,
            )

        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_rtio = ClockDomain()
        txoutclk_bufg = Signal()
        txoutclk_bufr = Signal()
        tx_bufr_div = int(qpll.config["clkin"]/self.rtio_clk_freq)
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=txoutclk_bufg),
            # TODO: use MMCM instead?
            Instance("BUFR", i_I=txoutclk_bufg, o_O=txoutclk_bufr,
                i_CE=1, p_BUFR_DIVIDE=str(tx_bufr_div)),
            Instance("BUFG", i_I=txoutclk_bufr, o_O=self.cd_rtio.clk),
            AsyncResetSynchronizer(self.cd_rtio, tx_reset_deglitched)
        ]

        self.comb += [
            txdata.eq(Cat(self.encoder.output[0], self.encoder.output[1])),
        ]
