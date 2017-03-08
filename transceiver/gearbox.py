from fractions import gcd

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer


def lcm(a, b):
    """Compute the lowest common multiple of a and b"""
    return int(a * b / gcd(a, b))


class Gearbox(Module):
    def __init__(self, iwidth, idomain, owidth, odomain):
        self.i = Signal(iwidth)
        self.o = Signal(owidth)

        # # #

        reset = Signal()
        cd_write = ClockDomain()
        cd_read = ClockDomain()
        self.comb += [
            cd_write.clk.eq(ClockSignal(idomain)),
            cd_read.clk.eq(ClockSignal(odomain)),
            reset.eq(ResetSignal(idomain) | ResetSignal(odomain))
        ]
        self.specials += [
            AsyncResetSynchronizer(cd_write, reset),
            AsyncResetSynchronizer(cd_read, reset)
        ]
        self.clock_domains += cd_write, cd_read

        storage = Signal(lcm(iwidth, owidth))
        wrchunks = len(storage)//iwidth
        rdchunks = len(storage)//owidth
        wrpointer = Signal(max=wrchunks, reset=0 if iwidth > owidth else wrchunks-1)
        rdpointer = Signal(max=rdchunks, reset=rdchunks-1 if iwidth > owidth else 0)

        self.sync.write += \
            If(wrpointer == wrchunks-1,
                wrpointer.eq(0)
            ).Else(
                wrpointer.eq(wrpointer + 1)
            )
        cases = {}
        for i in range(wrchunks):
            cases[i] = [storage[iwidth*i:iwidth*(i+1)].eq(self.i)]
        self.sync.write += Case(wrpointer, cases)


        self.sync.read += \
            If(rdpointer == rdchunks-1,
                rdpointer.eq(0)
            ).Else(
                rdpointer.eq(rdpointer + 1)
            )
        cases = {}
        for i in range(rdchunks):
            cases[i] = [self.o.eq(storage[owidth*i:owidth*(i+1)])]
        self.sync.read += Case(rdpointer, cases)
