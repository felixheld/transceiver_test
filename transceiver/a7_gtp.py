from collections import namedtuple
from math import ceil

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer
from litex.gen.genlib.cdc import MultiReg, PulseSynchronizer


QPLLSettings = namedtuple("QPLLSettings", "refclksel fbdiv fbdiv_45 refclk_div")


class QPLLChannel:
    def __init__(self):
        self.reset = Signal()
        self.lock = Signal()
        self.clk = Signal()
        self.refclk = Signal()


class QPLL(Module):
    def __init__(self, gtrefclk0, qpllsettings0, gtrefclk1=0, qpllsettings1=None):
        self.channels = []

        channel_settings = dict()
        for i, qpllsettings in enumerate((qpllsettings0, qpllsettings1)):
            def add_setting(k, v):
                channel_settings[k.replace("PLLX", "PLL"+str(i))] = v

            if qpllsettings is None:
                add_setting("i_PLLXPD", 1)
            else:
                channel = QPLLChannel()
                self.channels.append(channel)
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


class GTPRxInit(Module):
    def __init__(self, sys_clk_freq):
        self.rx_reset = Signal()
        self.rx_pma_reset_done = Signal()

        # DRPCLK must be driven by the system clock
        self.drpaddr = Signal(9)
        self.drpen = Signal()
        self.drpdi = Signal(16)
        self.drprdy = Signal()
        self.drpdo = Signal(16)
        self.drpwe = Signal()

        self.enable = Signal()
        self.restart = Signal()
        self.done = Signal()

        self.debug = Signal(8)

        # Handle async signals
        rx_reset = Signal()
        self.sync += self.rx_reset.eq(rx_reset)
        self.rx_reset.attr.add("no_retiming")
        rx_pma_reset_done = Signal()
        self.specials += MultiReg(self.rx_pma_reset_done, rx_pma_reset_done)

        drpvalue = Signal(16)
        drpmask = Signal()
        self.comb += [
            self.drpaddr.eq(0x011),
            If(drpmask,
                self.drpdi.eq(drpvalue & 0xf7ff)
            ).Else(
                self.drpdi.eq(drpvalue)
            )
        ]

        rx_pma_reset_done_r = Signal()
        self.sync += rx_pma_reset_done_r.eq(rx_pma_reset_done)

        fsm = FSM()
        self.submodules += fsm

        fsm.act("WAIT_ENABLE",
            self.debug.eq(0),
            If(self.enable, NextState("GTRXRESET"))
        )
        fsm.act("GTRXRESET",
            self.debug.eq(1),
            rx_reset.eq(1),
            NextState("DRP_READ_ISSUE")
        )
        fsm.act("DRP_READ_ISSUE",
            self.debug.eq(2),
            rx_reset.eq(1),
            self.drpen.eq(1),
            NextState("DRP_READ_WAIT")
        )
        fsm.act("DRP_READ_WAIT",
            self.debug.eq(3),
            rx_reset.eq(1),
            If(self.drprdy,
                NextValue(drpvalue, self.drpdo),
                NextState("DRP_MOD_ISSUE")
            )
        )
        fsm.act("DRP_MOD_ISSUE",
            self.debug.eq(4),
            rx_reset.eq(1),
            drpmask.eq(1),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_MOD_WAIT")
        )
        fsm.act("DRP_MOD_WAIT",
            self.debug.eq(5),
            rx_reset.eq(1),
            If(self.drprdy,
                NextState("WAIT_PMARST_FALL")
            )
        )
        fsm.act("WAIT_PMARST_FALL",
            self.debug.eq(6),
            If(rx_pma_reset_done_r & ~rx_pma_reset_done,
                NextState("DRP_RESTORE_ISSUE")
            )
        )
        fsm.act("DRP_RESTORE_ISSUE",
            self.debug.eq(7),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_RESTORE_WAIT")
        )
        fsm.act("DRP_RESTORE_WAIT",
            self.debug.eq(8),
            If(self.drprdy,
                NextState("DONE")
            )
        )
        fsm.act("DONE",
            self.debug.eq(9),
            self.done.eq(1),
            If(self.restart, NextState("WAIT_ENABLE"))
        )


class GTPTxInit(Module):
    def __init__(self, sys_clk_freq):
        self.qpll_reset = Signal()
        self.qpll_lock = Signal()
        self.tx_reset = Signal()
        self.done = Signal()

        self.debug = Signal(8)

        # Handle async signals
        qpll_reset = Signal()
        tx_reset = Signal()
        self.sync += [
            self.qpll_reset.eq(qpll_reset),
            self.tx_reset.eq(tx_reset)
        ]
        self.qpll_reset.attr.add("no_retiming")
        self.tx_reset.attr.add("no_retiming")
        qpll_lock = Signal()
        self.specials += MultiReg(self.qpll_lock, qpll_lock)

        # After configuration, transceiver resets have to stay low for
        # at least 500ns.
        # See https://www.xilinx.com/support/answers/43482.html
        timer_max = ceil(500e-9*sys_clk_freq)
        timer = Signal(max=timer_max+1)
        tick = Signal()
        self.sync += [
            tick.eq(0),
            If(timer == timer_max,
                tick.eq(1),
                timer.eq(0)
            ).Else(
                timer.eq(timer + 1)
            )
        ]

        fsm = FSM()
        self.submodules += fsm

        fsm.act("WAIT",
            self.debug.eq(0),
            If(tick, NextState("QPLL_RESET"))
        )
        fsm.act("QPLL_RESET",
            self.debug.eq(1),
            tx_reset.eq(1),
            qpll_reset.eq(1),
            If(tick, NextState("WAIT_QPLL_LOCK"))
        )
        fsm.act("WAIT_QPLL_LOCK",
            self.debug.eq(2),
            tx_reset.eq(1),
            If(qpll_lock & tick, NextState("DONE"))
        )
        fsm.act("DONE",
            self.debug.eq(3),
            self.done.eq(1)
        )


class GTP(Module):
    def __init__(self, qpll_channel, tx_pads, rx_pads, sys_clk_freq):
        self.rx_data = Signal(20)
        self.tx_data = Signal(20)

        self.clock_domains.cd_tx = ClockDomain()
        self.clock_domains.cd_rx = ClockDomain()
        self.clock_domains.cd_tx_half = ClockDomain(reset_less=True)
        self.clock_domains.cd_rx_half = ClockDomain(reset_less=True)

        # for specifying clock constraints. 62.5MHz clocks.
        self.txoutclk = Signal()
        self.rxoutclk = Signal()

        # # #

        # GTP transceiver
        tx_reset = Signal()
        tx_data = Signal(20)
        tx_reset_done = Signal()

        rx_reset = Signal()
        rx_data = Signal(20)
        rx_reset_done = Signal()
        rx_pma_reset_done = Signal()

        drpaddr = Signal(9)
        drpen = Signal()
        drpdi = Signal(16)
        drprdy = Signal()
        drpdo = Signal(16)
        drpwe = Signal()

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
            p_RXOUT_DIV                              =4,

            # TX Fabric Clock Output Control Attributes
            p_TXOUT_DIV                              =4,

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
            i_DRPADDR                       =drpaddr,
            i_DRPCLK                        =ClockSignal(),
            i_DRPDI                         =drpdi,
            o_DRPDO                         =drpdo,
            i_DRPEN                         =drpen,
            o_DRPRDY                        =drprdy,
            i_DRPWE                         =drpwe,
            # Clocking Ports
            i_RXSYSCLKSEL                    =0b00,
            i_TXSYSCLKSEL                    =0b00,
            # FPGA TX Interface Datapath Configuration
            i_TX8B10BEN                      =0,
            # GTPE2_CHANNEL Clocking Ports
            i_PLL0CLK                        =qpll_channel.clk,
            i_PLL0REFCLK                     =qpll_channel.refclk,
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
            i_RXUSERRDY                      =1,
            # RX Margin Analysis Ports
            #o_EYESCANDATAERROR               =,
            i_EYESCANMODE                    =0,
            i_EYESCANTRIGGER                 =0,
            # Receive Ports
            i_CLKRSVD0                       =0,
            i_CLKRSVD1                       =0,
            i_DMONFIFORESET                  =0,
            i_DMONITORCLK                    =0,
            o_RXPMARESETDONE                 =rx_pma_reset_done,
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
            o_RXDATA                         =Cat(self.rx_data[:8], self.rx_data[10:18]),
            i_RXUSRCLK                       =ClockSignal("rx"),
            i_RXUSRCLK2                      =ClockSignal("rx"),
            # Receive Ports - Pattern Checker Ports
            #o_RXPRBSERR                      =,
            i_RXPRBSSEL                      =0,
            # Receive Ports - Pattern Checker ports
            i_RXPRBSCNTRESET                 =0,
            # Receive Ports - RX 8B/10B Decoder Ports
            #o_RXCHARISCOMMA                  =,
            o_RXCHARISK                      =Cat(self.rx_data[8], self.rx_data[18]),
            o_RXDISPERR                      =Cat(self.rx_data[9], self.rx_data[19]),
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
            i_RXDLYSRESET                    =0,
            #o_RXDLYSRESETDONE                =,
            i_RXPHALIGN                      =0,
            #o_RXPHALIGNDONE                  =,
            i_RXPHALIGNEN                    =0,
            i_RXPHDLYPD                      =0,
            i_RXPHDLYRESET                   =0,
            #o_RXPHMONITOR                    =,
            i_RXPHOVRDEN                     =0,
            #o_RXPHSLIPMONITOR                =,
            #o_RXSTATUS                       =,
            i_RXSYNCALLIN                    =0,
            #o_RXSYNCDONE                     =,
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
            i_GTRXRESET                      =rx_reset,
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
            i_RXPOLARITY                     =0,
            # Receive Ports -RX Initialization and Reset Ports
            o_RXRESETDONE                    =rx_reset_done,
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
            i_GTTXRESET                      =tx_reset,
            #o_PCSRSVDOUT                     =,
            i_TXUSERRDY                      =1,
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
            i_TXDATA                         =Cat(self.tx_data[:8], self.tx_data[10:18]),
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
            i_TXCHARDISPMODE                 =Cat(self.tx_data[9], self.tx_data[19]),
            i_TXCHARDISPVAL                  =Cat(self.tx_data[8], self.tx_data[18]),
            i_TXCHARISK                      =0,
            # Transmit Ports - TX Buffer Bypass Ports
            i_TXDLYBYPASS                    =1,
            i_TXDLYEN                        =0,
            i_TXDLYHOLD                      =0,
            i_TXDLYOVRDEN                    =0,
            i_TXDLYSRESET                    =0,
            #o_TXDLYSRESETDONE                =,
            i_TXDLYUPDOWN                    =0,
            i_TXPHALIGN                      =0,
            #o_TXPHALIGNDONE                  =,
            i_TXPHALIGNEN                    =0,
            i_TXPHDLYPD                      =0,
            i_TXPHDLYRESET                   =0,
            i_TXPHINIT                       =0,
            #o_TXPHINITDONE                   =,
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
            o_TXRESETDONE                    =tx_reset_done,
            # Transmit Ports - TX OOB signalling Ports
            #o_TXCOMFINISH                    =,
            i_TXCOMINIT                      =0,
            i_TXCOMSAS                       =0,
            i_TXCOMWAKE                      =0,
            i_TXPDELECIDLEMODE               =0,
            # Transmit Ports - TX Polarity Control Ports
            i_TXPOLARITY                     =0,
            # Transmit Ports - TX Receiver Detection Ports
            i_TXDETECTRX                     =0,
            # Transmit Ports - pattern Generator Ports
            i_TXPRBSSEL                      =0
        )
        self.specials += Instance("GTPE2_CHANNEL", **xilinx_mess)

        self.specials += Instance("BUFG", i_I=self.txoutclk, o_O=self.cd_tx.clk)
        self.specials += Instance("BUFG", i_I=self.rxoutclk, o_O=self.cd_rx.clk)
        self.specials += AsyncResetSynchronizer(self.cd_tx, ~qpll_channel.lock)

        # Transceiver init
        tx_init = GTPTxInit(sys_clk_freq)
        self.submodules.tx_init = tx_init
        self.comb += [
            qpll_channel.reset.eq(tx_init.qpll_reset),
            tx_init.qpll_lock.eq(qpll_channel.lock),
            tx_reset.eq(tx_init.tx_reset)
        ]

        rx_init = GTPRxInit(sys_clk_freq)
        self.submodules.rx_init = rx_init
        self.comb += [
            rx_init.enable.eq(tx_init.done),
            rx_reset.eq(rx_init.rx_reset),

            rx_init.rx_pma_reset_done.eq(rx_pma_reset_done),
            drpaddr.eq(rx_init.drpaddr),
            drpen.eq(rx_init.drpen),
            drpdi.eq(rx_init.drpdi),
            rx_init.drprdy.eq(drprdy),
            rx_init.drpdo.eq(drpdo),
            drpwe.eq(rx_init.drpwe)
        ]
        ps_restart = PulseSynchronizer("tx", "sys")
        self.submodules += ps_restart
        self.comb += [
            ps_restart.i.eq(0), # FIXME
            rx_init.restart.eq(ps_restart.o)
        ]

        # Unlike CDRs from serious vendors, Xilinx CDRs can't tell you
        # when they are locked. Assume CDR lock time is 50,000 UI,
        # as per DS183. The Xilinx "wizards" also assume that.
        cdr_lock_time = round(sys_clk_freq*50e3/1.25e9)
        cdr_lock_counter = Signal(max=cdr_lock_time+1)
        cdr_locked = Signal()
        self.sync += [
            If(rx_reset,
                cdr_locked.eq(0),
                cdr_lock_counter.eq(0)
            ).Elif(cdr_lock_counter != cdr_lock_time,
                cdr_lock_counter.eq(cdr_lock_counter + 1)
            ).Else(
                cdr_locked.eq(1)
            ),
        ]
        self.specials += AsyncResetSynchronizer(self.cd_rx, ~cdr_locked)