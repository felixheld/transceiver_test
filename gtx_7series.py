from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.interconnect.csr import *

from line_coding import Encoder, Decoder

from gtx_7series_init import *


class GTXChannelPLL(Module):
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
GTXChannel PLL
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


class GTX(Module):
    def __init__(self, cpll, tx_pads, rx_pads, sys_clk_freq):
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

        # # #

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTXInit(sys_clk_freq, False)
        # RX receives restart commands from RTIO domain
        rx_init = ClockDomainsRenamer("rtio")(
            GTXInit(cpll.refclk_freq, True))
        self.submodules += tx_init, rx_init
        self.comb += tx_init.cplllock.eq(cpll.lock), \
                     rx_init.cplllock.eq(cpll.lock)

        txdata = Signal(20)
        rxdata = Signal(20)
        self.specials += \
            Instance("GTXE2_CHANNEL",
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

                # CPLL
                p_CPLL_CFG=0xBC07DC,
                p_CPLL_FBDIV=cpll.config["n2"],
                p_CPLL_FBDIV_45=cpll.config["n1"],
                p_CPLL_REFCLK_DIV=cpll.config["m"],
                p_RXOUT_DIV=cpll.config["d"],
                p_TXOUT_DIV=cpll.config["d"],
                o_CPLLLOCK=cpll.lock,
                i_CPLLLOCKEN=1,
                i_CPLLREFCLKSEL=0b001,
                i_TSTIN=2**20-1,
                i_GTREFCLK0=cpll.refclk,

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
                o_TXPHALIGNDONE=tx_init.Xxphaligndone,
                i_TXUSERRDY=tx_init.Xxuserrdy,

                # TX data
                p_TX_DATA_WIDTH=20,
                p_TX_INT_DATAWIDTH=0,
                i_TXCHARDISPMODE=Cat(txdata[9], txdata[19]),
                i_TXCHARDISPVAL=Cat(txdata[8], txdata[18]),
                i_TXDATA=Cat(txdata[:8], txdata[10:18]),
                i_TXUSRCLK=ClockSignal("rtio"),
                i_TXUSRCLK2=ClockSignal("rtio"),

                # TX electrical
                i_TXBUFDIFFCTRL=0b100,
                i_TXDIFFCTRL=0b1000,

                # RX Startup/Reset
                i_GTRXRESET=rx_init.gtXxreset,
                o_RXRESETDONE=rx_init.Xxresetdone,
                i_RXDLYSRESET=rx_init.Xxdlysreset,
                o_RXDLYSRESETDONE=rx_init.Xxdlysresetdone,
                o_RXPHALIGNDONE=rx_init.Xxphaligndone,
                i_RXUSERRDY=rx_init.Xxuserrdy,

                # RX AFE
                p_RX_DFE_XYD_CFG=0,
                i_RXDFEXYDEN=1,
                i_RXDFEXYDHOLD=0,
                i_RXDFEXYDOVRDEN=0,
                i_RXLPMEN=0,

                # RX clock
                p_RXBUF_EN="FALSE",
                p_RX_XCLK_SEL="RXUSR",
                i_RXDDIEN=1,
                i_RXSYSCLKSEL=0b00,
                i_RXOUTCLKSEL=0b010,
                o_RXOUTCLK=self.rxoutclk,
                i_RXUSRCLK=ClockSignal("rtio_rx"),
                i_RXUSRCLK2=ClockSignal("rtio_rx"),
                p_RXCDR_CFG=0x03000023FF10100020,

                # RX Clock Correction Attributes
                p_CLK_CORRECT_USE="FALSE",
                p_CLK_COR_SEQ_1_1=0b0100000000,
                p_CLK_COR_SEQ_2_1=0b0100000000,
                p_CLK_COR_SEQ_1_ENABLE=0b1111,
                p_CLK_COR_SEQ_2_ENABLE=0b1111,

                # RX data
                p_RX_DATA_WIDTH=20,
                p_RX_INT_DATAWIDTH=0,
                o_RXDISPERR=Cat(rxdata[9], rxdata[19]),
                o_RXCHARISK=Cat(rxdata[8], rxdata[18]),
                o_RXDATA=Cat(rxdata[:8], rxdata[10:18]),

                # Pads
                i_GTXRXP=rx_pads.p,
                i_GTXRXN=rx_pads.n,
                o_GTXTXP=tx_pads.p,
                o_GTXTXN=tx_pads.n,
            )

        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_rtio = ClockDomain()
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=self.cd_rtio.clk),
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
            txdata.eq(Cat(self.encoder.output[0], self.encoder.output[1])),
            self.decoders[0].input.eq(rxdata[:10]),
            self.decoders[1].input.eq(rxdata[10:])
        ]

        clock_aligner = BruteforceClockAligner(0b0101111100, cpll.refclk_freq)
        self.submodules += clock_aligner
        self.comb += [
            clock_aligner.rxdata.eq(rxdata),
            rx_init.restart.eq(clock_aligner.restart),
            self.rx_ready.eq(clock_aligner.ready)
        ]


class RXSynchronizer(Module, AutoCSR):
    """Delays the data received in the rtio_rx by a configurable amount
    so that it meets s/h in the rtio domain, and recapture it in the rtio
    domain. This has fixed latency.

    Since Xilinx doesn't provide decent on-chip delay lines, we implement the
    delay with MMCM that provides a clock and a finely configurable phase, used
    to resample the data.

    The phase has to be determined either empirically or by making sense of the
    Xilinx scriptures (when existent) and should be constant for a given design
    placement.
    """
    def __init__(self, rtio_clk_freq, initial_phase=0.0):
        self.phase_shift = CSR()
        self.phase_shift_done = CSRStatus()

        self.clock_domains.cd_rtio_delayed = ClockDomain()

        mmcm_output = Signal()
        mmcm_fb = Signal()
        mmcm_locked = Signal()
        # maximize VCO frequency to maximize phase shift resolution
        mmcm_mult = 1200e6//rtio_clk_freq
        self.specials += [
            Instance("MMCME2_ADV",
                p_CLKIN1_PERIOD=1e9/rtio_clk_freq,
                i_CLKIN1=ClockSignal("rtio_rx"),
                i_RST=ResetSignal("rtio_rx"),
                i_CLKINSEL=1,  # yes, 1=CLKIN1 0=CLKIN2

                p_CLKFBOUT_MULT_F=mmcm_mult,
                p_CLKOUT0_DIVIDE_F=mmcm_mult,
                p_CLKOUT0_PHASE=initial_phase,
                p_DIVCLK_DIVIDE=1,

                # According to Xilinx, there is no guarantee of input/output
                # phase relationship when using internal feedback. We assume
                # here that the input/ouput skew is constant to save BUFGs.
                o_CLKFBOUT=mmcm_fb,
                i_CLKFBIN=mmcm_fb,

                p_CLKOUT0_USE_FINE_PS="TRUE",
                o_CLKOUT0=mmcm_output,
                o_LOCKED=mmcm_locked,

                i_PSCLK=ClockSignal(),
                i_PSEN=self.phase_shift.re,
                i_PSINCDEC=self.phase_shift.r,
                o_PSDONE=self.phase_shift_done.status,
            ),
            Instance("BUFR", i_I=mmcm_output, o_O=self.cd_rtio_delayed.clk),
            AsyncResetSynchronizer(self.cd_rtio_delayed, ~mmcm_locked)
        ]

    def resync(self, signal):
        delayed = Signal.like(signal, related=signal)
        synchronized = Signal.like(signal, related=signal)
        self.sync.rtio_delayed += delayed.eq(signal)
        self.sync.rtio += synchronized.eq(delayed)
        return synchronized
