from math import ceil

from litex.gen import *
from litex.gen.genlib.cdc import MultiReg, PulseSynchronizer
from litex.gen.genlib.misc import WaitTimer

class GTPTXInit(Module):
    def __init__(self, sys_clk_freq):
        self.done = Signal()
        self.restart = Signal()

        self.debug = Signal(8)

        # GTP signals
        self.plllock = Signal()
        self.pllreset = Signal()
        self.gttxreset = Signal()
        self.txresetdone = Signal()
        self.txdlysreset = Signal()
        self.txdlysresetdone = Signal()
        self.txphinit = Signal()
        self.txphinitdone = Signal()
        self.txphalign = Signal()
        self.txphaligndone = Signal()
        self.txdlyen = Signal()
        self.txuserrdy = Signal()

        # # #

        # Double-latch transceiver asynch outputs
        plllock = Signal()
        txresetdone = Signal()
        txdlysresetdone = Signal()
        txphinitdone = Signal()
        txphaligndone = Signal()
        self.specials += [
            MultiReg(self.plllock, plllock),
            MultiReg(self.txresetdone, txresetdone),
            MultiReg(self.txdlysresetdone, txdlysresetdone),
            MultiReg(self.txphinitdone, txphinitdone),
            MultiReg(self.txphaligndone, txphaligndone)
        ]

        # Deglitch FSM outputs driving transceiver asynch inputs
        gttxreset = Signal()
        txdlysreset = Signal()
        txphinit = Signal()
        txphalign = Signal()
        txdlyen = Signal()
        txuserrdy = Signal()
        self.sync += [
            self.gttxreset.eq(gttxreset),
            self.txdlysreset.eq(txdlysreset),
            self.txphinit.eq(txphinit),
            self.txphalign.eq(txphalign),
            self.txdlyen.eq(txdlyen),
            self.txuserrdy.eq(txuserrdy)
        ]
        self.gttxreset.attr.add("no_retiming")

        # After configuration, transceiver resets have to stay low for
        # at least 500ns (see AR43482)
        startup_cycles = ceil(500e-9*sys_clk_freq)
        startup_timer = WaitTimer(startup_cycles)
        self.submodules += startup_timer

        startup_fsm = ResetInserter()(FSM(reset_state="WAIT"))
        self.submodules += startup_fsm

        ready_timer = WaitTimer(int(1e-3*sys_clk_freq))
        self.submodules += ready_timer
        self.comb += [
            ready_timer.wait.eq(~self.done & ~startup_fsm.reset),
            startup_fsm.reset.eq(self.restart | ready_timer.done)
        ]

        txphaligndone_r = Signal(reset=1)
        txphaligndone_rising = Signal()
        self.sync += txphaligndone_r.eq(txphaligndone)
        self.comb += txphaligndone_rising.eq(txphaligndone & ~txphaligndone_r)

        startup_fsm.act("WAIT",
            self.debug.eq(0),
            self.pllreset.eq(1),
            startup_timer.wait.eq(1),
            If(startup_timer.done,
                NextState("RESET_ALL")
            )
        )

        startup_fsm.act("RESET_ALL",
            self.debug.eq(0),
            self.pllreset.eq(1),
            gttxreset.eq(1),
            NextState("RELEASE_PLL_RESET")
        )

        # Release GTP reset and wait for GTP resetdone
        # (from UG482, GTP is reset on falling edge
        # of gttxreset)

        startup_fsm.act("RELEASE_PLL_RESET",
            self.debug.eq(1),
            gttxreset.eq(1),
            If(plllock,
                NextState("RELEASE_GTP_RESET")
            )
        )
        startup_fsm.act("RELEASE_GTP_RESET",
            self.debug.eq(2),
            txuserrdy.eq(1),
            If(txresetdone, NextState("ALIGN"))
        )
        # Delay alignment
        startup_fsm.act("ALIGN",
            self.debug.eq(3),
            txuserrdy.eq(1),
            txdlysreset.eq(1),
            If(txdlysresetdone,
                NextState("PHINIT")
            )
        )
        # Phase init
        startup_fsm.act("PHINIT",
            self.debug.eq(4),
            txuserrdy.eq(1),
            txphinit.eq(1),
            If(txphinitdone,
                NextState("PHALIGN")
            )
        )
        # Phase align
        startup_fsm.act("PHALIGN",
            self.debug.eq(5),
            txuserrdy.eq(1),
            txphalign.eq(1),
            If(txphaligndone_rising,
                NextState("DLYEN")
            )
        )
        startup_fsm.act("DLYEN",
            self.debug.eq(6),
            txuserrdy.eq(1),
            txdlyen.eq(1),
            If(txphaligndone_rising,
                NextState("READY")
            )
        )
        startup_fsm.act("READY",
            self.debug.eq(12),
            txuserrdy.eq(1),
            self.done.eq(1),
            If(self.restart, NextState("RESET_ALL"))
        )


class GTPRXInit(Module):
    def __init__(self, sys_clk_freq):
        self.done = Signal()
        self.restart = Signal()

        self.debug = Signal(8)

        # GTP signals
        self.plllock = Signal()
        self.pllreset = Signal()
        self.gtrxreset = Signal()
        self.rxresetdone = Signal()
        self.rxdlysreset = Signal()
        self.rxdlysresetdone = Signal()
        self.rxphalign = Signal()
        self.rxdlyen = Signal()
        self.rxuserrdy = Signal()
        self.rxsyncdone = Signal()

        self.drpaddr = Signal(9)
        self.drpen = Signal()
        self.drpdi = Signal(16)
        self.drprdy = Signal()
        self.drpdo = Signal(16)
        self.drpwe = Signal()

        self.rx_pma_reset_done = Signal()

        # # #

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

        # Double-latch transceiver asynch outputs
        plllock = Signal()
        rxresetdone = Signal()
        rxdlysresetdone = Signal()
        rxsyncdone = Signal()
        self.specials += [
            MultiReg(self.plllock, plllock),
            MultiReg(self.rxresetdone, rxresetdone),
            MultiReg(self.rxdlysresetdone, rxdlysresetdone),
            MultiReg(self.rxsyncdone, rxsyncdone)
        ]

        # Deglitch FSM outputs driving transceiver asynch inputs
        gtrxreset = Signal()
        rxdlysreset = Signal()
        rxphalign = Signal()
        rxdlyen = Signal()
        rxuserrdy = Signal()
        self.sync += [
            self.gtrxreset.eq(gtrxreset),
            self.rxdlysreset.eq(rxdlysreset),
            self.rxphalign.eq(rxphalign),
            self.rxdlyen.eq(rxdlyen),
            self.rxuserrdy.eq(rxuserrdy)
        ]
        self.gtrxreset.attr.add("no_retiming")

        # After configuration, transceiver resets have to stay low for
        # at least 500ns (see AR43482)
        startup_cycles = ceil(500e-9*sys_clk_freq)
        startup_timer = WaitTimer(startup_cycles)
        self.submodules += startup_timer

        startup_fsm = ResetInserter()(FSM(reset_state="WAIT"))
        self.submodules += startup_fsm

        ready_timer = WaitTimer(int(4e-3*sys_clk_freq))
        self.submodules += ready_timer
        self.comb += [
            ready_timer.wait.eq(~self.done & ~startup_fsm.reset),
            startup_fsm.reset.eq(self.restart | ready_timer.done)
        ]

        cdr_stable_timer = WaitTimer(1024)
        self.submodules += cdr_stable_timer

        startup_fsm.act("WAIT",
            self.debug.eq(0),
            self.pllreset.eq(1),
            startup_timer.wait.eq(1),
            If(startup_timer.done,
                NextState("RESET_ALL")
            )
        )

        startup_fsm.act("RESET_ALL",
            self.debug.eq(0),
            self.pllreset.eq(1),
            gtrxreset.eq(1),
            NextState("RELEASE_PLL_RESET")
        )

        # Release GTP reset and wait for GTP resetdone
        # (from UG482, GTP is reset on falling edge
        # of gtrxreset)
        startup_fsm.act("RELEASE_PLL_RESET",
            self.debug.eq(1),
            gtrxreset.eq(1),
            If(plllock,
                NextState("DRP_READ_ISSUE")
            )
        )
        startup_fsm.act("DRP_READ_ISSUE",
            self.debug.eq(2),
            gtrxreset.eq(1),
            self.drpen.eq(1),
            NextState("DRP_READ_WAIT")
        )
        startup_fsm.act("DRP_READ_WAIT",
            self.debug.eq(3),
            gtrxreset.eq(1),
            If(self.drprdy,
                NextValue(drpvalue, self.drpdo),
                NextState("DRP_MOD_ISSUE")
            )
        )
        startup_fsm.act("DRP_MOD_ISSUE",
            self.debug.eq(4),
            gtrxreset.eq(1),
            drpmask.eq(1),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_MOD_WAIT")
        )
        startup_fsm.act("DRP_MOD_WAIT",
            self.debug.eq(5),
            gtrxreset.eq(1),
            If(self.drprdy,
                NextState("WAIT_PMARST_FALL")
            )
        )
        startup_fsm.act("WAIT_PMARST_FALL",
            self.debug.eq(6),
            rxuserrdy.eq(1),
            If(rx_pma_reset_done_r & ~rx_pma_reset_done,
                NextState("DRP_RESTORE_ISSUE")
            )
        )
        startup_fsm.act("DRP_RESTORE_ISSUE",
            self.debug.eq(7),
            rxuserrdy.eq(1),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_RESTORE_WAIT")
        )
        startup_fsm.act("DRP_RESTORE_WAIT",
            self.debug.eq(8),
            rxuserrdy.eq(1),
            If(self.drprdy,
                NextState("WAIT_GTP_RESET_DONE")
            )
        )
        startup_fsm.act("WAIT_GTP_RESET_DONE",
            self.debug.eq(9),
            rxuserrdy.eq(1),
            cdr_stable_timer.wait.eq(1),
            If(rxresetdone & cdr_stable_timer.done,
                NextState("ALIGN")
            )
        )
        # Delay alignment
        startup_fsm.act("ALIGN",
            self.debug.eq(10),
            rxuserrdy.eq(1),
            rxdlysreset.eq(1),
            If(rxdlysresetdone,
                NextState("WAIT_ALIGN_DONE")
            )
        )
        startup_fsm.act("WAIT_ALIGN_DONE",
            self.debug.eq(11),
            rxuserrdy.eq(1),
            If(rxsyncdone,
                NextState("READY")
            )
        )
        startup_fsm.act("READY",
            self.debug.eq(12),
            rxuserrdy.eq(1),
            self.done.eq(1),
            If(self.restart,
                NextState("RESET_ALL")
            )
        )
