from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.interconnect.csr import *

from transceiver.line_coding import Encoder, Decoder
from transceiver.gth_ultrascale_init import *


class GTHChannelPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate):
        self.refclk = refclk
        self.refclk_freq = refclk_freq
        self.reset = Signal()
        self.lock = Signal()
        self.config = self.compute_config(refclk_freq, linerate)

    @classmethod
    def compute_config(self, refclk_freq, linerate):
        for n1 in 4, 5:
            for n2 in 1, 2, 3, 4, 5:
                for m in 1, 2:
                    vco_freq = refclk_freq*(n1*n2)/m
                    print(vco_freq/1e9)
                    #if 2.0e9 <= vco_freq <= 6.25e9:
                    if vco_freq <= 6.25e9:
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
GTHChannel PLL
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


class GTH(Module):
    def __init__(self, cpll, tx_pads, rx_pads, sys_clk_freq):
        self.submodules.encoder = ClockDomainsRenamer("rtio")(
            Encoder(2, True))

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()

        # # #

        assert cpll.config["linerate"]/cpll.config["clkin"] == 20

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTHInit(sys_clk_freq, False)
        self.submodules += tx_init
        self.comb += [
            tx_init.plllock.eq(cpll.lock),
            cpll.reset.eq(tx_init.pllreset)
        ]

        txdata = Signal(20)
        self.specials += \
            Instance("GTHE3_CHANNEL",
                # Reset modes
                i_GTRESETSEL=0,
                i_RESETOVRD=0,

                i_TXPOLARITY=1,

                # PMA Attributes
                p_PMA_RSV1=0xf800,
                p_RX_BIAS_CFG0=0x0AB4,
                p_RX_CM_TRIM=0b1010,
                p_RX_CLK25_DIV=5,
                p_TX_CLK25_DIV=5,

                # Power-Down Attributes
                p_PD_TRANS_TIME_FROM_P2=0x3c,
                p_PD_TRANS_TIME_NONE_P2=0x19,
                p_PD_TRANS_TIME_TO_P2=0x64,

                # CPLL
                p_CPLL_CFG0=0x67f8,
                p_CPLL_CFG1=0xa4ac,
                p_CPLL_CFG2=0xf007,
                p_CPLL_CFG3=0x0000,
                p_CPLL_FBDIV=cpll.config["n2"],
                p_CPLL_FBDIV_45=cpll.config["n1"],
                p_CPLL_REFCLK_DIV=cpll.config["m"],
                p_RXOUT_DIV=cpll.config["d"],
                p_TXOUT_DIV=cpll.config["d"],
                i_CPLLRESET=0,
                i_CPLLPD=cpll.reset,
                o_CPLLLOCK=cpll.lock,
                i_CPLLLOCKEN=1,
                i_CPLLREFCLKSEL=0b001,
                i_TSTIN=2**20-1,
                i_GTREFCLK0=cpll.refclk,

                # QPLL
                i_QPLL0CLK=0,
                i_QPLL0REFCLK=0,
                i_QPLL1CLK=0,
                i_QPLL1REFCLK=0,

                # TX clock
                p_TXBUF_EN="FALSE",
                p_TX_XCLK_SEL="TXUSR",
                o_TXOUTCLK=self.txoutclk,
                i_TXSYSCLKSEL=0b00,
                i_TXPLLCLKSEL=0b00,
                i_TXOUTCLKSEL=0b11,

                # disable RX
                i_RXPD=0b11,

                # TX Startup/Reset
                i_GTTXRESET=tx_init.gtXxreset,
                o_TXRESETDONE=tx_init.Xxresetdone,
                i_TXDLYSRESET=tx_init.Xxdlysreset,
                o_TXDLYSRESETDONE=tx_init.Xxdlysresetdone,
                o_TXPHALIGNDONE=tx_init.Xxphaligndone,
                i_TXUSERRDY=tx_init.Xxuserrdy,

                # TX data
                p_TX_DATA_WIDTH=20,
                p_TX_INT_DATAWIDTH=0,
                i_TXCTRL0=Cat(txdata[8], txdata[18]),
                i_TXCTRL1=Cat(txdata[9], txdata[19]),
                i_TXDATA=Cat(txdata[:8], txdata[10:18]),
                i_TXUSRCLK=ClockSignal("rtio"),
                i_TXUSRCLK2=ClockSignal("rtio"),

                # TX electrical
                i_TXBUFDIFFCTRL=0b000,
                i_TXDIFFCTRL=0b1100,

                # Pads
                o_GTHTXP=tx_pads.p,
                o_GTHTXN=tx_pads.n
            )

        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_rtio = ClockDomain()
        self.specials += [
            Instance("BUFG_GT", i_I=self.txoutclk, o_O=self.cd_rtio.clk),
            AsyncResetSynchronizer(self.cd_rtio, tx_reset_deglitched)
        ]

        self.comb += [
            txdata.eq(Cat(self.encoder.output[0], self.encoder.output[1])),
        ]
