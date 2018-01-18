from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from litex.soc.interconnect.csr import *
from litex.soc.cores.code_8b10b import Encoder, Decoder

from transceiver.gtp_7series_init import GTPInit
from transceiver.clock_aligner import BruteforceClockAligner

from transceiver.prbs import *


class GTPQuadPLL(Module):
    def __init__(self, refclk, refclk_freq, linerate, refclk_from_fabric=False):
        self.clk = Signal()
        self.refclk = Signal()
        self.reset = Signal()
        self.lock = Signal()
        self.config = self.compute_config(refclk_freq, linerate)

        # # #

        self.specials += \
            Instance("GTPE2_COMMON",
                # common
                i_GTREFCLK0=0 if refclk_from_fabric else refclk,
                i_GTGREFCLK0=refclk if refclk_from_fabric else 0,
                i_BGBYPASSB=1,
                i_BGMONITORENB=1,
                i_BGPDB=1,
                i_BGRCALOVRD=0b11111,
                i_RCALENB=1,

                # pll0
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

                # pll1 (not used: power down)
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


class GTP(Module, AutoCSR):
    def __init__(self, qpll, tx_pads, rx_pads, sys_clk_freq,
                 clock_aligner=True, internal_loopback=False,
                 tx_polarity=0, rx_polarity=0):
        self.tx_produce_square_wave = CSRStorage()
        self.tx_prbs_config = CSRStorage(2)

        self.rx_prbs_config = CSRStorage(2)
        self.rx_prbs_errors = CSRStatus(32)

        # # #

        self.submodules.encoder = ClockDomainsRenamer("tx")(
            Encoder(2, True))
        self.decoders = [ClockDomainsRenamer("rx")(
            Decoder(True)) for _ in range(2)]
        self.submodules += self.decoders

        self.rx_ready = Signal()

        # transceiver direct clock outputs
        # useful to specify clock constraints in a way palatable to Vivado
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        self.tx_clk_freq = qpll.config["linerate"]/20

        # control/status cdc
        tx_produce_square_wave = Signal()
        tx_prbs_config = Signal(2)

        rx_prbs_config = Signal(2)
        rx_prbs_errors = Signal(32)

        self.specials += [
            MultiReg(self.tx_produce_square_wave.storage, tx_produce_square_wave, "tx"),
            MultiReg(self.tx_prbs_config.storage, tx_prbs_config, "tx"),
        ]

        self.specials += [
            MultiReg(self.rx_prbs_config.storage, rx_prbs_config, "rx"),
            MultiReg(rx_prbs_errors, self.rx_prbs_errors.status, "sys"), # FIXME
        ]

        # # #

        # TX generates RTIO clock, init must be in system domain
        tx_init = GTPInit(sys_clk_freq, False)
        # RX receives restart commands from RTIO domain
        rx_init = ClockDomainsRenamer("tx")(
            GTPInit(self.tx_clk_freq, True))
        self.submodules += tx_init, rx_init
        # debug
        self.tx_init = tx_init
        self.rx_init = rx_init
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

        # Work around Python's 255 argument limitation.
        xilinx_mess = dict(
            # Simulation-Only Attributes
            p_SIM_RECEIVER_DETECT_PASS   ="TRUE",
            p_SIM_TX_EIDLE_DRIVE_LEVEL   ="X",
            p_SIM_RESET_SPEEDUP          ="FALSE",
            p_SIM_VERSION                ="2.0",

            # RX Byte and Word Alignment Attributes
            p_ALIGN_COMMA_DOUBLE                     ="FALSE",
            p_ALIGN_COMMA_ENABLE                     =0b1111111111,
            p_ALIGN_COMMA_WORD                       =1,
            p_ALIGN_MCOMMA_DET                       ="TRUE",
            p_ALIGN_MCOMMA_VALUE                     =0b1010000011,
            p_ALIGN_PCOMMA_DET                       ="TRUE",
            p_ALIGN_PCOMMA_VALUE                     =0b0101111100,
            p_SHOW_REALIGN_COMMA                     ="TRUE",
            p_RXSLIDE_AUTO_WAIT                      =7,
            p_RXSLIDE_MODE                           ="OFF",
            p_RX_SIG_VALID_DLY                       =10,

            # RX 8B/10B Decoder Attributes
            p_RX_DISPERR_SEQ_MATCH                   ="FALSE",
            p_DEC_MCOMMA_DETECT                      ="FALSE",
            p_DEC_PCOMMA_DETECT                      ="FALSE",
            p_DEC_VALID_COMMA_ONLY                   ="FALSE",

            # RX Clock Correction Attributes
            p_CBCC_DATA_SOURCE_SEL                   ="ENCODED",
            p_CLK_COR_SEQ_2_USE                      ="FALSE",
            p_CLK_COR_KEEP_IDLE                      ="FALSE",
            p_CLK_COR_MAX_LAT                        =9,
            p_CLK_COR_MIN_LAT                        =7,
            p_CLK_COR_PRECEDENCE                     ="TRUE",
            p_CLK_COR_REPEAT_WAIT                    =0,
            p_CLK_COR_SEQ_LEN                        =1,
            p_CLK_COR_SEQ_1_ENABLE                   =0b1111,
            p_CLK_COR_SEQ_1_1                        =0b0100000000,
            p_CLK_COR_SEQ_1_2                        =0b0000000000,
            p_CLK_COR_SEQ_1_3                        =0b0000000000,
            p_CLK_COR_SEQ_1_4                        =0b0000000000,
            p_CLK_CORRECT_USE                        ="FALSE",
            p_CLK_COR_SEQ_2_ENABLE                   =0b1111,
            p_CLK_COR_SEQ_2_1                        =0b0100000000,
            p_CLK_COR_SEQ_2_2                        =0b0000000000,
            p_CLK_COR_SEQ_2_3                        =0b0000000000,
            p_CLK_COR_SEQ_2_4                        =0b0000000000,

            # RX Channel Bonding Attributes
            p_CHAN_BOND_KEEP_ALIGN                   ="FALSE",
            p_CHAN_BOND_MAX_SKEW                     =1,
            p_CHAN_BOND_SEQ_LEN                      =1,
            p_CHAN_BOND_SEQ_1_1                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_2                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_3                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_4                      =0b0000000000,
            p_CHAN_BOND_SEQ_1_ENABLE                 =0b1111,
            p_CHAN_BOND_SEQ_2_1                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_2                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_3                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_4                      =0b0000000000,
            p_CHAN_BOND_SEQ_2_ENABLE                 =0b1111,
            p_CHAN_BOND_SEQ_2_USE                    ="FALSE",
            p_FTS_DESKEW_SEQ_ENABLE                  =0b1111,
            p_FTS_LANE_DESKEW_CFG                    =0b1111,
            p_FTS_LANE_DESKEW_EN                     ="FALSE",

            # RX Margin Analysis Attributes
            p_ES_CONTROL                             =0b000000,
            p_ES_ERRDET_EN                           ="FALSE",
            p_ES_EYE_SCAN_EN                         ="FALSE",
            p_ES_HORZ_OFFSET                         =0x010,
            p_ES_PMA_CFG                             =0b0000000000,
            p_ES_PRESCALE                            =0b00000,
            p_ES_QUALIFIER                           =0x00000000000000000000,
            p_ES_QUAL_MASK                           =0x00000000000000000000,
            p_ES_SDATA_MASK                          =0x00000000000000000000,
            p_ES_VERT_OFFSET                         =0b000000000,

            # FPGA RX Interface Attributes
            p_RX_DATA_WIDTH                          =20,

            # PMA Attributes
            p_OUTREFCLK_SEL_INV                      =0b11,
            p_PMA_RSV                                =0x00000333,
            p_PMA_RSV2                               =0x00002040,
            p_PMA_RSV3                               =0b00,
            p_PMA_RSV4                               =0b0000,
            p_RX_BIAS_CFG                            =0b0000111100110011,
            p_DMONITOR_CFG                           =0x000A00,
            p_RX_CM_SEL                              =0b01,
            p_RX_CM_TRIM                             =0b0000,
            p_RX_DEBUG_CFG                           =0b00000000000000,
            p_RX_OS_CFG                              =0b0000010000000,
            p_TERM_RCAL_CFG                          =0b100001000010000,
            p_TERM_RCAL_OVRD                         =0b000,
            p_TST_RSV                                =0x00000000,
            p_RX_CLK25_DIV                           =5,
            p_TX_CLK25_DIV                           =5,
            p_UCODEER_CLR                            =0b0,

            # PCI Express Attributes
            p_PCS_PCIE_EN                            ="FALSE",

            # PCS Attributes
            p_PCS_RSVD_ATTR                          =0x000000000000,

            # RX Buffer Attributes
            p_RXBUF_ADDR_MODE                        ="FAST",
            p_RXBUF_EIDLE_HI_CNT                     =0b1000,
            p_RXBUF_EIDLE_LO_CNT                     =0b0000,
            p_RXBUF_EN                               ="TRUE",
            p_RX_BUFFER_CFG                          =0b000000,
            p_RXBUF_RESET_ON_CB_CHANGE               ="TRUE",
            p_RXBUF_RESET_ON_COMMAALIGN              ="FALSE",
            p_RXBUF_RESET_ON_EIDLE                   ="FALSE",
            p_RXBUF_RESET_ON_RATE_CHANGE             ="TRUE",
            p_RXBUFRESET_TIME                        =0b00001,
            p_RXBUF_THRESH_OVFLW                     =61,
            p_RXBUF_THRESH_OVRD                      ="FALSE",
            p_RXBUF_THRESH_UNDFLW                    =4,
            p_RXDLY_CFG                              =0x001F,
            p_RXDLY_LCFG                             =0x030,
            p_RXDLY_TAP_CFG                          =0x0000,
            p_RXPH_CFG                               =0xC00002,
            p_RXPHDLY_CFG                            =0x084020,
            p_RXPH_MONITOR_SEL                       =0b00000,
            p_RX_XCLK_SEL                            ="RXREC",
            p_RX_DDI_SEL                             =0b000000,
            p_RX_DEFER_RESET_BUF_EN                  ="TRUE",

            # CDR Attributes
            p_RXCDR_CFG                              =0x0001107FE086021101010,
            p_RXCDR_FR_RESET_ON_EIDLE                =0b0,
            p_RXCDR_HOLD_DURING_EIDLE                =0b0,
            p_RXCDR_PH_RESET_ON_EIDLE                =0b0,
            p_RXCDR_LOCK_CFG                         =0b001001,

            # RX Initialization and Reset Attributes
            p_RXCDRFREQRESET_TIME                    =0b00001,
            p_RXCDRPHRESET_TIME                      =0b00001,
            p_RXISCANRESET_TIME                      =0b00001,
            p_RXPCSRESET_TIME                        =0b00001,
            p_RXPMARESET_TIME                        =0b00011,

            # RX OOB Signaling Attributes
            p_RXOOB_CFG                              =0b0000110,

            # RX Gearbox Attributes
            p_RXGEARBOX_EN                           ="FALSE",
            p_GEARBOX_MODE                           =0b000,

            # PRBS Detection Attribute
            p_RXPRBS_ERR_LOOPBACK                    =0b0,

            # Power-Down Attributes
            p_PD_TRANS_TIME_FROM_P2                  =0x03c,
            p_PD_TRANS_TIME_NONE_P2                  =0x3c,
            p_PD_TRANS_TIME_TO_P2                    =0x64,

            # RX OOB Signaling Attributes
            p_SAS_MAX_COM                            =64,
            p_SAS_MIN_COM                            =36,
            p_SATA_BURST_SEQ_LEN                     =0b0101,
            p_SATA_BURST_VAL                         =0b100,
            p_SATA_EIDLE_VAL                         =0b100,
            p_SATA_MAX_BURST                         =8,
            p_SATA_MAX_INIT                          =21,
            p_SATA_MAX_WAKE                          =7,
            p_SATA_MIN_BURST                         =4,
            p_SATA_MIN_INIT                          =12,
            p_SATA_MIN_WAKE                          =4,

            # RX Fabric Clock Output Control Attributes
            p_TRANS_TIME_RATE                        =0x0E,

            # TX Buffer Attributes
            p_TXBUF_EN                               ="TRUE",
            p_TXBUF_RESET_ON_RATE_CHANGE             ="TRUE",
            p_TXDLY_CFG                              =0x001F,
            p_TXDLY_LCFG                             =0x030,
            p_TXDLY_TAP_CFG                          =0x0000,
            p_TXPH_CFG                               =0x0780,
            p_TXPHDLY_CFG                            =0x084020,
            p_TXPH_MONITOR_SEL                       =0b00000,
            p_TX_XCLK_SEL                            ="TXOUT",

            # FPGA TX Interface Attributes
            p_TX_DATA_WIDTH                          =20,

            # TX Configurable Driver Attributes
            p_TX_DEEMPH0                             =0b000000,
            p_TX_DEEMPH1                             =0b000000,
            p_TX_EIDLE_ASSERT_DELAY                  =0b110,
            p_TX_EIDLE_DEASSERT_DELAY                =0b100,
            p_TX_LOOPBACK_DRIVE_HIZ                  ="FALSE",
            p_TX_MAINCURSOR_SEL                      =0b0,
            p_TX_DRIVE_MODE                          ="DIRECT",
            p_TX_MARGIN_FULL_0                       =0b1001110,
            p_TX_MARGIN_FULL_1                       =0b1001001,
            p_TX_MARGIN_FULL_2                       =0b1000101,
            p_TX_MARGIN_FULL_3                       =0b1000010,
            p_TX_MARGIN_FULL_4                       =0b1000000,
            p_TX_MARGIN_LOW_0                        =0b1000110,
            p_TX_MARGIN_LOW_1                        =0b1000100,
            p_TX_MARGIN_LOW_2                        =0b1000010,
            p_TX_MARGIN_LOW_3                        =0b1000000,
            p_TX_MARGIN_LOW_4                        =0b1000000,

            # TX Gearbox Attributes
            p_TXGEARBOX_EN                           ="FALSE",

            # TX Initialization and Reset Attributes
            p_TXPCSRESET_TIME                        =0b00001,
            p_TXPMARESET_TIME                        =0b00001,

            # TX Receiver Detection Attributes
            p_TX_RXDETECT_CFG                        =0x1832,
            p_TX_RXDETECT_REF                        =0b100,

            # JTAG Attributes
            p_ACJTAG_DEBUG_MODE                      =0b0,
            p_ACJTAG_MODE                            =0b0,
            p_ACJTAG_RESET                           =0b0,

            # CDR Attributes
            p_CFOK_CFG                               =0x49000040E80,
            p_CFOK_CFG2                              =0b0100000,
            p_CFOK_CFG3                              =0b0100000,
            p_CFOK_CFG4                              =0b0,
            p_CFOK_CFG5                              =0x0,
            p_CFOK_CFG6                              =0b0000,
            p_RXOSCALRESET_TIME                      =0b00011,
            p_RXOSCALRESET_TIMEOUT                   =0b00000,

            # PMA Attributes
            p_CLK_COMMON_SWING                       =0b0,
            p_RX_CLKMUX_EN                           =0b1,
            p_TX_CLKMUX_EN                           =0b1,
            p_ES_CLK_PHASE_SEL                       =0b0,
            p_USE_PCS_CLK_PHASE_SEL                  =0b0,
            p_PMA_RSV6                               =0b0,
            p_PMA_RSV7                               =0b0,

            # TX Configuration Driver Attributes
            p_TX_PREDRIVER_MODE                      =0b0,
            p_PMA_RSV5                               =0b0,
            p_SATA_PLL_CFG                           ="VCO_3000MHZ",

            # RX Fabric Clock Output Control Attributes
            p_RXOUT_DIV                              =qpll.config["d"],

            # TX Fabric Clock Output Control Attributes
            p_TXOUT_DIV                              =qpll.config["d"],

            # RX Phase Interpolator Attributes
            p_RXPI_CFG0                              =0b000,
            p_RXPI_CFG1                              =0b1,
            p_RXPI_CFG2                              =0b1,

            # RX Equalizer Attributes
            p_ADAPT_CFG0                             =0x00000,
            p_RXLPMRESET_TIME                        =0b0001111,
            p_RXLPM_BIAS_STARTUP_DISABLE             =0b0,
            p_RXLPM_CFG                              =0b0110,
            p_RXLPM_CFG1                             =0b0,
            p_RXLPM_CM_CFG                           =0b0,
            p_RXLPM_GC_CFG                           =0b111100010,
            p_RXLPM_GC_CFG2                          =0b001,
            p_RXLPM_HF_CFG                           =0b00001111110000,
            p_RXLPM_HF_CFG2                          =0b01010,
            p_RXLPM_HF_CFG3                          =0b0000,
            p_RXLPM_HOLD_DURING_EIDLE                =0b0,
            p_RXLPM_INCM_CFG                         =0b0,
            p_RXLPM_IPCM_CFG                         =0b1,
            p_RXLPM_LF_CFG                           =0b000000001111110000,
            p_RXLPM_LF_CFG2                          =0b01010,
            p_RXLPM_OSINT_CFG                        =0b100,

            # TX Phase Interpolator PPM Controller Attributes
            p_TXPI_CFG0                              =0b00,
            p_TXPI_CFG1                              =0b00,
            p_TXPI_CFG2                              =0b00,
            p_TXPI_CFG3                              =0b0,
            p_TXPI_CFG4                              =0b0,
            p_TXPI_CFG5                              =0b000,
            p_TXPI_GREY_SEL                          =0b0,
            p_TXPI_INVSTROBE_SEL                     =0b0,
            p_TXPI_PPMCLK_SEL                        ="TXUSRCLK2",
            p_TXPI_PPM_CFG                           =0x00,
            p_TXPI_SYNFREQ_PPM                       =0b001,

            # LOOPBACK Attributes
            p_LOOPBACK_CFG                           =0b0,
            p_PMA_LOOPBACK_CFG                       =0b0,

            # RX OOB Signalling Attributes
            p_RXOOB_CLK_CFG                          ="PMA",

            # TX OOB Signalling Attributes
            p_TXOOB_CFG                              =0b0,

            # RX Buffer Attributes
            p_RXSYNC_MULTILANE                       =0b0,
            p_RXSYNC_OVRD                            =0b0,
            p_RXSYNC_SKIP_DA                         =0b0,

            # TX Buffer Attributes
            p_TXSYNC_MULTILANE                       =0b0,
            p_TXSYNC_OVRD                            =0b0,
            p_TXSYNC_SKIP_DA                         =0b0
        )
        xilinx_mess.update(
            # CPLL Ports
            i_GTRSVD                         =0b0000000000000000,
            i_PCSRSVDIN                      =0b0000000000000000,
            i_TSTIN                          =0b11111111111111111111,
            # Channel - DRP Ports
            i_DRPADDR                       =rx_init.drpaddr,
            i_DRPCLK                        =ClockSignal("tx"),
            i_DRPDI                         =rx_init.drpdi,
            o_DRPDO                         =rx_init.drpdo,
            i_DRPEN                         =rx_init.drpen,
            o_DRPRDY                        =rx_init.drprdy,
            i_DRPWE                         =rx_init.drpwe,
            # Clocking Ports
            i_RXSYSCLKSEL                    =0b00,
            i_TXSYSCLKSEL                    =0b00,
            # FPGA TX Interface Datapath Configuration
            i_TX8B10BEN                      =0,
            # GTPE2_CHANNEL Clocking Ports
            i_PLL0CLK                        =qpll.clk,
            i_PLL0REFCLK                     =qpll.refclk,
            i_PLL1CLK                        =0,
            i_PLL1REFCLK                     =0,
            # Loopback Ports
            i_LOOPBACK                       =0,
            # PCI Express Ports
            #o_PHYSTATUS                      =,
            i_RXRATE                         =0,
            #o_RXVALID                        =,
            # PMA Reserved Ports
            i_PMARSVDIN3                     =0b0,
            i_PMARSVDIN4                     =0b0,
            # Power-Down Ports
            i_RXPD                           =0b00,
            i_TXPD                           =0b00,
            # RX 8B/10B Decoder Ports
            i_SETERRSTATUS                   =0,
            # RX Initialization and Reset Ports
            i_EYESCANRESET                   =0,
            i_RXUSERRDY                      =rx_init.Xxuserrdy,
            # RX Margin Analysis Ports
            #o_EYESCANDATAERROR               =,
            i_EYESCANMODE                    =0,
            i_EYESCANTRIGGER                 =0,
            # Receive Ports
            i_CLKRSVD0                       =0,
            i_CLKRSVD1                       =0,
            i_DMONFIFORESET                  =0,
            i_DMONITORCLK                    =0,
            o_RXPMARESETDONE                 =rx_init.rx_pma_reset_done,
            i_SIGVALIDCLK                    =0,
            # Receive Ports - CDR Ports
            i_RXCDRFREQRESET                 =0,
            i_RXCDRHOLD                      =0,
            #o_RXCDRLOCK                      =,
            i_RXCDROVRDEN                    =0,
            i_RXCDRRESET                     =0,
            i_RXCDRRESETRSV                  =0,
            i_RXOSCALRESET                   =0,
            i_RXOSINTCFG                     =0b0010,
            #o_RXOSINTDONE                    =,
            i_RXOSINTHOLD                    =0,
            i_RXOSINTOVRDEN                  =0,
            i_RXOSINTPD                      =0,
            #o_RXOSINTSTARTED                 =,
            i_RXOSINTSTROBE                  =0,
            #o_RXOSINTSTROBESTARTED           =,
            i_RXOSINTTESTOVRDEN              =0,
            # Receive Ports - Clock Correction Ports
            #o_RXCLKCORCNT                    =,
            # Receive Ports - FPGA RX Interface Datapath Configuration
            i_RX8B10BEN                      =0,
            # Receive Ports - FPGA RX Interface Ports
            o_RXDATA                         =Cat(rxdata[:8], rxdata[10:18]),
            i_RXUSRCLK                       =ClockSignal("rx"),
            i_RXUSRCLK2                      =ClockSignal("rx"),
            # Receive Ports - Pattern Checker Ports
            #o_RXPRBSERR                      =,
            i_RXPRBSSEL                      =0,
            # Receive Ports - Pattern Checker ports
            i_RXPRBSCNTRESET                 =0,
            # Receive Ports - RX 8B/10B Decoder Ports
            #o_RXCHARISCOMMA                  =,
            o_RXCHARISK                      =Cat(rxdata[8], rxdata[18]),
            o_RXDISPERR                      =Cat(rxdata[9], rxdata[19]),
            #o_RXNOTINTABLE                   =,
            # Receive Ports - RX AFE Ports
            i_GTPRXN                         =rx_pads.n,
            i_GTPRXP                         =rx_pads.p,
            i_PMARSVDIN2                     =0b0,
            #o_PMARSVDOUT0                    =,
            #o_PMARSVDOUT1                    =,
            # Receive Ports - RX Buffer Bypass Ports
            i_RXBUFRESET                     =0,
            #o_RXBUFSTATUS                    =,
            i_RXDDIEN                        =0,
            i_RXDLYBYPASS                    =1,
            i_RXDLYEN                        =0,
            i_RXDLYOVRDEN                    =0,
            i_RXDLYSRESET                    =rx_init.Xxdlysreset,
            o_RXDLYSRESETDONE                =rx_init.Xxdlysresetdone,
            i_RXPHALIGN                      =0,
            o_RXPHALIGNDONE                  =rx_init.Xxphaligndone,
            i_RXPHALIGNEN                    =0,
            i_RXPHDLYPD                      =0,
            i_RXPHDLYRESET                   =0,
            #o_RXPHMONITOR                    =,
            i_RXPHOVRDEN                     =0,
            #o_RXPHSLIPMONITOR                =,
            #o_RXSTATUS                       =,
            i_RXSYNCALLIN                    =rx_init.Xxphaligndone,
            o_RXSYNCDONE                     =rx_init.Xxsyncdone,
            i_RXSYNCIN                       =0,
            i_RXSYNCMODE                     =0,
            #o_RXSYNCOUT                      =,
            # Receive Ports - RX Byte and Word Alignment Ports
            #o_RXBYTEISALIGNED                =,
            #o_RXBYTEREALIGN                  =,
            #o_RXCOMMADET                     =,
            i_RXCOMMADETEN                   =0,
            i_RXMCOMMAALIGNEN                =0,
            i_RXPCOMMAALIGNEN                =0,
            i_RXSLIDE                        =0,
            # Receive Ports - RX Channel Bonding Ports
            #o_RXCHANBONDSEQ                  =,
            i_RXCHBONDEN                     =0,
            i_RXCHBONDI                      =0b0000,
            i_RXCHBONDLEVEL                  =0,
            i_RXCHBONDMASTER                 =0,
            #o_RXCHBONDO                      =,
            i_RXCHBONDSLAVE                  =0,
            # Receive Ports - RX Channel Bonding Ports
            #o_RXCHANISALIGNED                =,
            #o_RXCHANREALIGN                  =,
            # Receive Ports - RX Decision Feedback Equalizer
            #o_DMONITOROUT                    =,
            i_RXADAPTSELTEST                 =0,
            i_RXDFEXYDEN                     =0,
            i_RXOSINTEN                      =0b1,
            i_RXOSINTID0                     =0,
            i_RXOSINTNTRLEN                  =0,
            #o_RXOSINTSTROBEDONE              =,
            # Receive Ports - RX Driver,OOB signalling,Coupling and Eq.,CDR
            i_RXLPMLFOVRDEN                  =0,
            i_RXLPMOSINTNTRLEN               =0,
            # Receive Ports - RX Equalizer Ports
            i_RXLPMHFHOLD                    =0,
            i_RXLPMHFOVRDEN                  =0,
            i_RXLPMLFHOLD                    =0,
            i_RXOSHOLD                       =0,
            i_RXOSOVRDEN                     =0,
            # Receive Ports - RX Fabric ClocK Output Control Ports
            #o_RXRATEDONE                     =,
            # Receive Ports - RX Fabric Clock Output Control Ports
            i_RXRATEMODE                     =0b0,
            # Receive Ports - RX Fabric Output Control Ports
            o_RXOUTCLK                       =self.rxoutclk,
            #o_RXOUTCLKFABRIC                 =,
            #o_RXOUTCLKPCS                    =,
            i_RXOUTCLKSEL                    =0b010,
            # Receive Ports - RX Gearbox Ports
            #o_RXDATAVALID                    =,
            #o_RXHEADER                       =,
            #o_RXHEADERVALID                  =,
            #o_RXSTARTOFSEQ                   =,
            i_RXGEARBOXSLIP                  =0,
            # Receive Ports - RX Initialization and Reset Ports
            i_GTRXRESET                      =rx_init.gtXxreset,
            i_RXLPMRESET                     =0,
            i_RXOOBRESET                     =0,
            i_RXPCSRESET                     =0,
            i_RXPMARESET                     =0,
            # Receive Ports - RX OOB Signaling ports
            #o_RXCOMSASDET                    =,
            #o_RXCOMWAKEDET                   =,
            #o_RXCOMINITDET                   =,
            #o_RXELECIDLE                     =,
            i_RXELECIDLEMODE                 =0b11,
            # Receive Ports - RX Polarity Control Ports
            i_RXPOLARITY                     =rx_polarity,
            # Receive Ports -RX Initialization and Reset Ports
            o_RXRESETDONE                    =rx_init.Xxresetdone,
            # TX Buffer Bypass Ports
            i_TXPHDLYTSTCLK                  =0,
            # TX Configurable Driver Ports
            i_TXPOSTCURSOR                   =0b00000,
            i_TXPOSTCURSORINV                =0,
            i_TXPRECURSOR                    =0,
            i_TXPRECURSORINV                 =0,
            # TX Fabric Clock Output Control Ports
            i_TXRATEMODE                     =0,
            # TX Initialization and Reset Ports
            i_CFGRESET                       =0,
            i_GTTXRESET                      =tx_init.gtXxreset,
            #o_PCSRSVDOUT                     =,
            i_TXUSERRDY                      =tx_init.Xxuserrdy,
            # TX Phase Interpolator PPM Controller Ports
            i_TXPIPPMEN                      =0,
            i_TXPIPPMOVRDEN                  =0,
            i_TXPIPPMPD                      =0,
            i_TXPIPPMSEL                     =1,
            i_TXPIPPMSTEPSIZE                =0,
            # Transceiver Reset Mode Operation
            i_GTRESETSEL                     =0,
            i_RESETOVRD                      =0,
            # Transmit Ports
            #o_TXPMARESETDONE                 =,
            # Transmit Ports - Configurable Driver Ports
            i_PMARSVDIN0                     =0b0,
            i_PMARSVDIN1                     =0b0,
            # Transmit Ports - FPGA TX Interface Ports
            i_TXDATA                         =Cat(txdata[:8], txdata[10:18]),
            i_TXUSRCLK                       =ClockSignal("tx"),
            i_TXUSRCLK2                      =ClockSignal("tx"),
            # Transmit Ports - PCI Express Ports
            i_TXELECIDLE                     =0,
            i_TXMARGIN                       =0,
            i_TXRATE                         =0,
            i_TXSWING                        =0,
            # Transmit Ports - Pattern Generator Ports
            i_TXPRBSFORCEERR                 =0,
            # Transmit Ports - TX 8B/10B Encoder Ports
            i_TX8B10BBYPASS                  =0,
            i_TXCHARDISPMODE                 =Cat(txdata[9], txdata[19]),
            i_TXCHARDISPVAL                  =Cat(txdata[8], txdata[18]),
            i_TXCHARISK                      =0,
            # Transmit Ports - TX Buffer Bypass Ports
            i_TXDLYBYPASS                    =1,
            i_TXDLYEN                        =tx_init.Xxdlyen,
            i_TXDLYHOLD                      =0,
            i_TXDLYOVRDEN                    =0,
            i_TXDLYSRESET                    =tx_init.Xxdlysreset,
            o_TXDLYSRESETDONE                =tx_init.Xxdlysresetdone,
            i_TXDLYUPDOWN                    =0,
            i_TXPHALIGN                      =tx_init.Xxphalign,
            o_TXPHALIGNDONE                  =tx_init.Xxphaligndone,
            i_TXPHALIGNEN                    =1,
            i_TXPHDLYPD                      =0,
            i_TXPHDLYRESET                   =0,
            i_TXPHINIT                       =tx_init.Xxphinit,
            o_TXPHINITDONE                   =tx_init.Xxphinitdone,
            i_TXPHOVRDEN                     =0,
            # Transmit Ports - TX Buffer Ports
            #o_TXBUFSTATUS                    =,
            # Transmit Ports - TX Buffer and Phase Alignment Ports
            i_TXSYNCALLIN                    =0,
            #o_TXSYNCDONE                     =,
            i_TXSYNCIN                       =0,
            i_TXSYNCMODE                     =0,
            #o_TXSYNCOUT                      =,
            # Transmit Ports - TX Configurable Driver Ports
            o_GTPTXN                         =tx_pads.n,
            o_GTPTXP                         =tx_pads.p,
            i_TXBUFDIFFCTRL                  =0b100,
            i_TXDEEMPH                       =0,
            i_TXDIFFCTRL                     =0b1000,
            i_TXDIFFPD                       =0,
            i_TXINHIBIT                      =0,
            i_TXMAINCURSOR                   =0b0000000,
            i_TXPISOPD                       =0,
            # Transmit Ports - TX Fabric Clock Output Control Ports
            o_TXOUTCLK                       =self.txoutclk,
            #o_TXOUTCLKFABRIC                 =,
            #o_TXOUTCLKPCS                    =,
            i_TXOUTCLKSEL                    =0b010,
            #o_TXRATEDONE                     =,
            # Transmit Ports - TX Gearbox Ports
            #o_TXGEARBOXREADY                 =,
            i_TXHEADER                       =0,
            i_TXSEQUENCE                     =0,
            i_TXSTARTSEQ                     =0,
            # Transmit Ports - TX Initialization and Reset Ports
            i_TXPCSRESET                     =0,
            i_TXPMARESET                     =0,
            o_TXRESETDONE                    =tx_init.Xxresetdone,
            # Transmit Ports - TX OOB signalling Ports
            #o_TXCOMFINISH                    =,
            i_TXCOMINIT                      =0,
            i_TXCOMSAS                       =0,
            i_TXCOMWAKE                      =0,
            i_TXPDELECIDLEMODE               =0,
            # Transmit Ports - TX Polarity Control Ports
            i_TXPOLARITY                     =tx_polarity,
            # Transmit Ports - TX Receiver Detection Ports
            i_TXDETECTRX                     =0,
            # Transmit Ports - pattern Generator Ports
            i_TXPRBSSEL                      =0
        )
        self.specials += Instance("GTPE2_CHANNEL", **xilinx_mess)


        # tx clocking
        tx_reset_deglitched = Signal()
        tx_reset_deglitched.attr.add("no_retiming")
        self.sync += tx_reset_deglitched.eq(~tx_init.done)
        self.clock_domains.cd_tx = ClockDomain()
        txoutclk_bufg = Signal()
        txoutclk_bufr = Signal()
        tx_bufr_div = qpll.config["clkin"]/self.tx_clk_freq
        assert tx_bufr_div == int(tx_bufr_div)
        self.specials += [
            Instance("BUFG", i_I=self.txoutclk, o_O=txoutclk_bufg),
            # TODO: use MMCM instead?
            Instance("BUFR", i_I=txoutclk_bufg, o_O=txoutclk_bufr,
                i_CE=1, p_BUFR_DIVIDE=str(int(tx_bufr_div))),
            Instance("BUFG", i_I=txoutclk_bufr, o_O=self.cd_tx.clk),
            AsyncResetSynchronizer(self.cd_tx, tx_reset_deglitched)
        ]

        # rx clocking
        rx_reset_deglitched = Signal()
        rx_reset_deglitched.attr.add("no_retiming")
        self.sync.tx += rx_reset_deglitched.eq(~rx_init.done)
        self.clock_domains.cd_rx = ClockDomain()
        self.specials += [
            Instance("BUFG", i_I=self.rxoutclk, o_O=self.cd_rx.clk),
            AsyncResetSynchronizer(self.cd_rx, rx_reset_deglitched)
        ]

        # tx data and prbs
        self.submodules.tx_prbs = ClockDomainsRenamer("tx")(PRBSTX(20, True))
        self.comb += self.tx_prbs.config.eq(tx_prbs_config)
        self.comb += [
            self.tx_prbs.i.eq(Cat(*[self.encoder.output[i] for i in range(2)])),
            If(tx_produce_square_wave,
                # square wave @ linerate/20 for scope observation
                txdata.eq(0b11111111110000000000)
            ).Else(
                txdata.eq(self.tx_prbs.o)
            )
        ]

        # rx data and prbs
        self.submodules.rx_prbs = ClockDomainsRenamer("rx")(PRBSRX(20, True))
        self.comb += [
            self.rx_prbs.config.eq(rx_prbs_config),
            rx_prbs_errors.eq(self.rx_prbs.errors)
        ]
        self.comb += [
            self.decoders[0].input.eq(rxdata[:10]),
            self.decoders[1].input.eq(rxdata[10:]),
            self.rx_prbs.i.eq(rxdata)
        ]

        # clock alignment
        if clock_aligner:
            clock_aligner = BruteforceClockAligner(0b0101111100, self.tx_clk_freq, check_period=6e-3)
            self.submodules += clock_aligner
            self.comb += [
                clock_aligner.rxdata.eq(rxdata),
                rx_init.restart.eq(clock_aligner.restart),
                self.rx_ready.eq(clock_aligner.ready)
            ]
        else:
            self.comb += self.rx_ready.eq(rx_init.done)
