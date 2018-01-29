from collections import namedtuple

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.interconnect.csr import *
from litex.soc.cores.code_8b10b import Encoder, Decoder

from transceiver.gtp_7series_init import GTPTXInit, GTPRXInit
from transceiver.clock_aligner import BruteforceClockAligner


QPLLSettings = namedtuple("QPLLSettings", "refclksel fbdiv fbdiv_45 refclk_div")


class QPLLChannel:
    def __init__(self, index):
        self.index = index
        self.reset = Signal()
        self.lock = Signal()
        self.clk = Signal()
        self.refclk = Signal()


class QPLL(Module):
    def __init__(self, gtrefclk0, qpllsettings0, gtrefclk1=0, qpllsettings1=None):
        self.channels = []

        channel_settings = dict()
        for i, qpllsettings in enumerate((qpllsettings0, qpllsettings1)):
            channel = QPLLChannel(i)
            self.channels.append(channel)

            def add_setting(k, v):
                channel_settings[k.replace("PLLX", "PLL"+str(i))] = v

            if qpllsettings is None:
                add_setting("i_PLLXPD", 1)
            else:
                add_setting("i_PLLXPD", 0)
                add_setting("i_PLLXLOCKEN", 1)
                add_setting("i_PLLXREFCLKSEL", qpllsettings.refclksel)
                add_setting("p_PLLX_FBDIV", qpllsettings.fbdiv)
                add_setting("p_PLLX_FBDIV_45", qpllsettings.fbdiv_45)
                add_setting("p_PLLX_REFCLK_DIV", qpllsettings.refclk_div)
                add_setting("i_PLLXRESET", channel.reset)
                add_setting("o_PLLXLOCK", channel.lock)
                add_setting("o_PLLXOUTCLK", channel.clk)
                add_setting("o_PLLXOUTREFCLK", channel.refclk)

        self.specials += \
            Instance("GTPE2_COMMON",
                i_GTREFCLK0=gtrefclk0,
                i_GTREFCLK1=gtrefclk1,
                i_BGBYPASSB=1,
                i_BGMONITORENB=1,
                i_BGPDB=1,
                i_BGRCALOVRD=0b11111,
                i_RCALENB=1,
                **channel_settings
            )


class GTPSingle(Module):
    def __init__(self, qpll_channel, pads, sys_clk_freq, rtio_clk_freq, mode):
        if mode != "master":
            raise NotImplementedError
        self.submodules.encoder = encoder = ClockDomainsRenamer("tx")(
            Encoder(2, True))
        self.submodules.decoders = decoders = [ClockDomainsRenamer("rx")(
            (Decoder(True))) for _ in range(2)]
        self.rx_ready = Signal()

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        # # #

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTPTXInit(sys_clk_freq)
        # RX receives restart commands from RTIO domain
        rx_init = ClockDomainsRenamer("tx")(GTPRXInit(rtio_clk_freq))
        self.submodules += tx_init, rx_init

        self.comb += [
            qpll_channel.reset.eq(tx_init.pllreset),
            tx_init.plllock.eq(qpll_channel.lock),
            rx_init.plllock.eq(qpll_channel.lock),
        ]

        txdata = Signal(20)
        rxdata = Signal(20)
        rxphaligndone = Signal()
        gtp_params = dict(
                # Reset modes
                i_GTRESETSEL=0,
                i_RESETOVRD=0,

                # DRP
                i_DRPADDR=rx_init.drpaddr,
                i_DRPCLK=ClockSignal("tx"),
                i_DRPDI=rx_init.drpdi,
                o_DRPDO=rx_init.drpdo,
                i_DRPEN=rx_init.drpen,
                o_DRPRDY=rx_init.drprdy,
                i_DRPWE=rx_init.drpwe,

                # PMA Attributes
                p_PMA_RSV=0x333,
                p_PMA_RSV2=0x2040,
                p_PMA_RSV3=0,
                p_PMA_RSV4=0,
                p_RX_BIAS_CFG=0b0000111100110011,
                p_RX_CM_SEL=0b01,
                p_RX_CM_TRIM=0b1010,
                p_RX_OS_CFG=0b10000000,
                p_RXLPM_IPCM_CFG=1,
                i_RXELECIDLEMODE=0b11,
                i_RXOSINTCFG=0b0010,
                i_RXOSINTEN=1,

                # Power-Down Attributes
                p_PD_TRANS_TIME_FROM_P2=0x3c,
                p_PD_TRANS_TIME_NONE_P2=0x3c,
                p_PD_TRANS_TIME_TO_P2=0x64,

                # TX clock
                p_TXBUF_EN="FALSE",
                p_TX_XCLK_SEL="TXUSR",
                o_TXOUTCLK=self.txoutclk,
                p_TXOUT_DIV=2,
                i_TXOUTCLKSEL=0b11,

                # TX Startup/Reset
                i_GTTXRESET=tx_init.gttxreset,
                o_TXRESETDONE=tx_init.txresetdone,
                p_TXSYNC_OVRD=1,
                i_TXDLYSRESET=tx_init.txdlysreset,
                o_TXDLYSRESETDONE=tx_init.txdlysresetdone,
                i_TXPHINIT=tx_init.txphinit,
                o_TXPHINITDONE=tx_init.txphinitdone,
                i_TXPHALIGNEN=1,
                i_TXPHALIGN=tx_init.txphalign,
                o_TXPHALIGNDONE=tx_init.txphaligndone,
                i_TXDLYEN=tx_init.txdlyen,
                i_TXUSERRDY=tx_init.txuserrdy,

                # TX data
                p_TX_DATA_WIDTH=20,
                i_TXCHARDISPMODE=Cat(txdata[9], txdata[19]),
                i_TXCHARDISPVAL=Cat(txdata[8], txdata[18]),
                i_TXDATA=Cat(txdata[:8], txdata[10:18]),
                i_TXUSRCLK=ClockSignal("tx"),
                i_TXUSRCLK2=ClockSignal("tx"),

                # TX electrical
                i_TXBUFDIFFCTRL=0b100,
                i_TXDIFFCTRL=0b1000,

                # RX Startup/Reset
                i_GTRXRESET=rx_init.gtrxreset,
                o_RXRESETDONE=rx_init.rxresetdone,
                i_RXDLYSRESET=rx_init.rxdlysreset,
                o_RXDLYSRESETDONE=rx_init.rxdlysresetdone,
                o_RXPHALIGNDONE=rxphaligndone,
                i_RXSYNCALLIN=rxphaligndone,
                i_RXUSERRDY=rx_init.rxuserrdy,
                i_RXSYNCIN=0,
                i_RXSYNCMODE=1,
                p_RXSYNC_MULTILANE=0,
                p_RXSYNC_OVRD=0,
                o_RXSYNCDONE=rx_init.rxsyncdone,
                p_RXPMARESET_TIME=0b11,
                o_RXPMARESETDONE=rx_init.rxpmaresetdone,

                # RX clock
                p_RX_CLK25_DIV=5,
                p_TX_CLK25_DIV=5,
                p_RX_XCLK_SEL="RXUSR",
                p_RXOUT_DIV=2,
                i_RXOUTCLKSEL=0b010,
                o_RXOUTCLK=self.rxoutclk,
                i_RXUSRCLK=ClockSignal("rx"),
                i_RXUSRCLK2=ClockSignal("rx"),
                p_RXCDR_CFG=0x0000107FE206001041010,
                p_RXPI_CFG1=1,
                p_RXPI_CFG2=1,

                # RX Clock Correction Attributes
                p_CLK_CORRECT_USE="FALSE",

                # RX data
                p_RXBUF_EN="FALSE",
                p_RXDLY_CFG=0x001f,
                p_RXDLY_LCFG=0x030,
                p_RXPHDLY_CFG=0x084020,
                p_RXPH_CFG=0xc00002,
                p_RX_DATA_WIDTH=20,
                i_RXCOMMADETEN=1,
                i_RXDLYBYPASS=0,
                i_RXDDIEN=1,
                o_RXDISPERR=Cat(rxdata[9], rxdata[19]),
                o_RXCHARISK=Cat(rxdata[8], rxdata[18]),
                o_RXDATA=Cat(rxdata[:8], rxdata[10:18]),

                # Pads
                i_GTPRXP=pads.rxp,
                i_GTPRXN=pads.rxn,
                o_GTPTXP=pads.txp,
                o_GTPTXN=pads.txn
            )
        if qpll_channel.index == 0:
            gtp_params.update(
                i_RXSYSCLKSEL=0b00,
                i_TXSYSCLKSEL=0b00,
                i_PLL0CLK=qpll_channel.clk,
                i_PLL0REFCLK=qpll_channel.refclk,
                i_PLL1CLK=0,
                i_PLL1REFCLK=0,
            )
        elif qpll_channel.index == 1:
            gtp_params.update(
                i_RXSYSCLKSEL=0b11,
                i_TXSYSCLKSEL=0b11,
                i_PLL0CLK=0,
                i_PLL0REFCLK=0,
                i_PLL1CLK=qpll_channel.clk,
                i_PLL1REFCLK=qpll_channel.refclk,
            )
        else:
            raise ValueError
        self.specials += Instance("GTPE2_CHANNEL", **gtp_params)

        # tx clocking
        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_tx = ClockDomain()
        if mode == "master":
            self.specials += Instance("BUFG", i_I=self.txoutclk, o_O=self.cd_tx.clk)
        self.specials += AsyncResetSynchronizer(self.cd_tx, tx_reset_deglitched)

        # rx clocking
        rx_reset_deglitched = Signal()
        rx_reset_deglitched.attr.add("no_retiming")
        self.sync.tx += rx_reset_deglitched.eq(~rx_init.done)
        self.clock_domains.cd_rx = ClockDomain()
        self.specials += [
            Instance("BUFG", i_I=self.rxoutclk, o_O=self.cd_rx.clk),
            AsyncResetSynchronizer(self.cd_rx, rx_reset_deglitched)
        ]

        # tx data
        self.comb += txdata.eq(Cat(*[encoder.output[i] for i in range(2)]))

        # rx data
        for i in range(2):
            self.comb += decoders[i].input.eq(rxdata[10*i:10*(i+1)])

        # clock alignment
        clock_aligner = BruteforceClockAligner(0b0101111100, rtio_clk_freq, check_period=10e-3)
        self.submodules += clock_aligner
        self.comb += [
            clock_aligner.rxdata.eq(rxdata),
            rx_init.restart.eq(clock_aligner.restart),
            self.rx_ready.eq(clock_aligner.ready)
        ]

