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
        self.gtXxreset = Signal()
        self.Xxresetdone = Signal()
        self.Xxdlysreset = Signal()
        self.Xxdlysresetdone = Signal()
        self.Xxphinit = Signal()
        self.Xxphinitdone = Signal()
        self.Xxphalign = Signal()
        self.Xxphaligndone = Signal()
        self.Xxdlyen = Signal()
        self.Xxuserrdy = Signal()
        self.Xxsyncdone = Signal()

        # # #

        # Double-latch transceiver asynch outputs
        plllock = Signal()
        Xxresetdone = Signal()
        Xxdlysresetdone = Signal()
        Xxphinitdone = Signal()
        Xxphaligndone = Signal()
        Xxsyncdone = Signal()
        self.specials += [
            MultiReg(self.plllock, plllock),
            MultiReg(self.Xxresetdone, Xxresetdone),
            MultiReg(self.Xxdlysresetdone, Xxdlysresetdone),
            MultiReg(self.Xxphinitdone, Xxphinitdone),
            MultiReg(self.Xxphaligndone, Xxphaligndone),
            MultiReg(self.Xxsyncdone, Xxsyncdone)
        ]

        # Deglitch FSM outputs driving transceiver asynch inputs
        gtXxreset = Signal()
        Xxdlysreset = Signal()
        Xxphinit = Signal()
        Xxphalign = Signal()
        Xxdlyen = Signal()
        Xxuserrdy = Signal()
        self.sync += [
            self.gtXxreset.eq(gtXxreset),
            self.Xxdlysreset.eq(Xxdlysreset),
            self.Xxphinit.eq(Xxphinit),
            self.Xxphalign.eq(Xxphalign),
            self.Xxdlyen.eq(Xxdlyen),
            self.Xxuserrdy.eq(Xxuserrdy)
        ]
        self.gtXxreset.attr.add("no_retiming")

        # After configuration, transceiver resets have to stay low for
        # at least 500ns (see AR43482)
        startup_cycles = ceil(500e-9*sys_clk_freq)
        startup_timer = WaitTimer(startup_cycles)
        self.submodules += startup_timer

        startup_fsm = ResetInserter()(FSM(reset_state="WAIT"))
        self.submodules += startup_fsm

        ready_timer = WaitTimer(int(sys_clk_freq/1000))
        self.submodules += ready_timer
        self.comb += [
            ready_timer.wait.eq(~self.done & ~startup_fsm.reset),
            startup_fsm.reset.eq(self.restart | ready_timer.done)
        ]

        Xxphaligndone_r = Signal(reset=1)
        Xxphaligndone_rising = Signal()
        self.sync += Xxphaligndone_r.eq(Xxphaligndone)
        self.comb += Xxphaligndone_rising.eq(Xxphaligndone & ~Xxphaligndone_r)

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
            gtXxreset.eq(1),
            NextState("RELEASE_PLL_RESET")
        )

        # Release GTP reset and wait for GTP resetdone
        # (from UG482, GTP is reset on falling edge
        # of gtXxreset)

        startup_fsm.act("RELEASE_PLL_RESET",
            self.debug.eq(1),
            gtXxreset.eq(1),
            If(plllock,
                NextState("RELEASE_GTP_RESET")
            )
        )
        startup_fsm.act("RELEASE_GTP_RESET",
            self.debug.eq(2),
            Xxuserrdy.eq(1),
            If(Xxresetdone, NextState("ALIGN"))
        )
        # Delay alignment
        startup_fsm.act("ALIGN",
            self.debug.eq(3),
            Xxuserrdy.eq(1),
            Xxdlysreset.eq(1),
            If(Xxdlysresetdone,
                NextState("PHINIT")
            )
        )
        # Phase init
        startup_fsm.act("PHINIT",
            self.debug.eq(4),
            Xxuserrdy.eq(1),
            Xxphinit.eq(1),
            If(Xxphinitdone,
                NextState("PHALIGN")
            )
        )
        # Phase align
        startup_fsm.act("PHALIGN",
            self.debug.eq(5),
            Xxuserrdy.eq(1),
            Xxphalign.eq(1),
            If(Xxphaligndone_rising,
                NextState("DLYEN")
            )
        )
        startup_fsm.act("DLYEN",
            self.debug.eq(6),
            Xxuserrdy.eq(1),
            Xxdlyen.eq(1),
            If(Xxphaligndone_rising,
                NextState("READY")
            )
        )
        startup_fsm.act("READY",
            self.debug.eq(12),
            Xxuserrdy.eq(1),
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
        self.gtXxreset = Signal()
        self.Xxresetdone = Signal()
        self.Xxdlysreset = Signal()
        self.Xxdlysresetdone = Signal()
        self.Xxphinit = Signal()
        self.Xxphinitdone = Signal()
        self.Xxphalign = Signal()
        self.Xxphaligndone = Signal()
        self.Xxdlyen = Signal()
        self.Xxuserrdy = Signal()
        self.Xxsyncdone = Signal()

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
        Xxresetdone = Signal()
        Xxdlysresetdone = Signal()
        Xxphinitdone = Signal()
        Xxphaligndone = Signal()
        Xxsyncdone = Signal()
        self.specials += [
            MultiReg(self.plllock, plllock),
            MultiReg(self.Xxresetdone, Xxresetdone),
            MultiReg(self.Xxdlysresetdone, Xxdlysresetdone),
            MultiReg(self.Xxphinitdone, Xxphinitdone),
            MultiReg(self.Xxphaligndone, Xxphaligndone),
            MultiReg(self.Xxsyncdone, Xxsyncdone)
        ]

        # Deglitch FSM outputs driving transceiver asynch inputs
        gtXxreset = Signal()
        Xxdlysreset = Signal()
        Xxphinit = Signal()
        Xxphalign = Signal()
        Xxdlyen = Signal()
        Xxuserrdy = Signal()
        self.sync += [
            self.gtXxreset.eq(gtXxreset),
            self.Xxdlysreset.eq(Xxdlysreset),
            self.Xxphinit.eq(Xxphinit),
            self.Xxphalign.eq(Xxphalign),
            self.Xxdlyen.eq(Xxdlyen),
            self.Xxuserrdy.eq(Xxuserrdy)
        ]
        self.gtXxreset.attr.add("no_retiming")

        # After configuration, transceiver resets have to stay low for
        # at least 500ns (see AR43482)
        startup_cycles = ceil(500e-9*sys_clk_freq)
        startup_timer = WaitTimer(startup_cycles)
        self.submodules += startup_timer

        startup_fsm = ResetInserter()(FSM(reset_state="WAIT"))
        self.submodules += startup_fsm

        ready_timer = WaitTimer(int(sys_clk_freq/1000))
        self.submodules += ready_timer
        self.comb += [
            ready_timer.wait.eq(~self.done & ~startup_fsm.reset),
            startup_fsm.reset.eq(self.restart | ready_timer.done)
        ]

        cdr_stable_timer = WaitTimer(1024)
        self.submodules += cdr_stable_timer

        Xxphaligndone_r = Signal(reset=1)
        Xxphaligndone_rising = Signal()
        self.sync += Xxphaligndone_r.eq(Xxphaligndone)
        self.comb += Xxphaligndone_rising.eq(Xxphaligndone & ~Xxphaligndone_r)

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
            gtXxreset.eq(1),
            NextState("RELEASE_PLL_RESET")
        )

        # Release GTP reset and wait for GTP resetdone
        # (from UG482, GTP is reset on falling edge
        # of gtXxreset)
        startup_fsm.act("RELEASE_PLL_RESET",
            self.debug.eq(1),
            gtXxreset.eq(1),
            If(plllock,
                NextState("DRP_READ_ISSUE")
            )
        )
        startup_fsm.act("DRP_READ_ISSUE",
            self.debug.eq(2),
            gtXxreset.eq(1),
            self.drpen.eq(1),
            NextState("DRP_READ_WAIT")
        )
        startup_fsm.act("DRP_READ_WAIT",
            self.debug.eq(3),
            gtXxreset.eq(1),
            If(self.drprdy,
                NextValue(drpvalue, self.drpdo),
                NextState("DRP_MOD_ISSUE")
            )
        )
        startup_fsm.act("DRP_MOD_ISSUE",
            self.debug.eq(4),
            gtXxreset.eq(1),
            drpmask.eq(1),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_MOD_WAIT")
        )
        startup_fsm.act("DRP_MOD_WAIT",
            self.debug.eq(5),
            gtXxreset.eq(1),
            If(self.drprdy,
                NextState("WAIT_PMARST_FALL")
            )
        )
        startup_fsm.act("WAIT_PMARST_FALL",
            self.debug.eq(6),
            Xxuserrdy.eq(1),
            If(1,
            #If(rx_pma_reset_done_r & ~rx_pma_reset_done, # FIXME!
                NextState("DRP_RESTORE_ISSUE")
            )
        )
        startup_fsm.act("DRP_RESTORE_ISSUE",
            self.debug.eq(7),
            Xxuserrdy.eq(1),
            self.drpen.eq(1),
            self.drpwe.eq(1),
            NextState("DRP_RESTORE_WAIT")
        )
        startup_fsm.act("DRP_RESTORE_WAIT",
            self.debug.eq(8),
            Xxuserrdy.eq(1),
            If(self.drprdy,
                NextState("WAIT_GTP_RESET_DONE")
            )
        )
        startup_fsm.act("WAIT_GTP_RESET_DONE",
            self.debug.eq(9),
            Xxuserrdy.eq(1),
            cdr_stable_timer.wait.eq(1),
            If(cdr_stable_timer.done,
            #If(Xxresetdone & cdr_stable_timer.done,
                NextState("ALIGN")
            )
        )
        # Delay alignment
        startup_fsm.act("ALIGN",
            self.debug.eq(10),
            Xxuserrdy.eq(1),
            Xxdlysreset.eq(1),
            If(Xxdlysresetdone,
                NextState("WAIT_ALIGN_DONE")
            )
        )
        startup_fsm.act("WAIT_ALIGN_DONE",
            self.debug.eq(11),
            Xxuserrdy.eq(1),
            If(1,
            #If(Xxsyncdone, # FIXME!
                NextState("READY")
            )
        )
        startup_fsm.act("READY",
            self.debug.eq(12),
            Xxuserrdy.eq(1),
            self.done.eq(1),
            If(self.restart, NextState("RESET_ALL"))
        )
