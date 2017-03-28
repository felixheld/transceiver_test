from litex.gen import *

import sys
sys.path.append("../")

from wishbone import packet
from wishbone import etherbone

from litex.soc.interconnect.wishbone import SRAM


class DUT(Module):
    def __init__(self):
        # wishbone slave
        slave_core = packet.Core(int(100e6))
        slave_port = slave_core.crossbar.get_port(0x01)
        slave_etherbone = etherbone.Etherbone(mode="slave")
        self.submodules += slave_core, slave_etherbone
        self.comb += [
            slave_port.source.connect(slave_etherbone.sink),
            slave_etherbone.source.connect(slave_port.sink)
        ]

        # wishbone master
        master_core = packet.Core(int(100e6))
        master_port = master_core.crossbar.get_port(0x01)
        master_etherbone = etherbone.Etherbone(mode="master")
        master_sram = SRAM(1024, bus=master_etherbone.wishbone.bus)
        self.submodules += master_core, master_etherbone, master_sram
        self.comb += [
            master_port.source.connect(master_etherbone.sink),
            master_etherbone.source.connect(master_port.sink)
        ]

        # connect core directly
        self.comb += [
            master_core.source.connect(slave_core.sink),
            slave_core.source.connect(master_core.sink)
        ]

        # expose wishbone slave
        self.wishbone = slave_etherbone.wishbone.bus

def main_generator(dut):
    for i in range(8):
        yield from dut.wishbone.write(0x100 + i, i)
    for i in range(8):
        data = (yield from dut.wishbone.read(0x100 + i))
        print("0x{:08x}".format(data))

dut = DUT()
run_simulation(dut, main_generator(dut), vcd_name="sim.vcd")
