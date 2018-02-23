#!/usr/bin/env python3

import os
import sys
sys.path.append("../")

from migen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform

from migen.genlib.io import CRG

from transceiver.gth_ultrascale import GTHChannelPLL, GTH


_io = [
    ("clk100", 0, Pins("X")),
    ("gth_refclk", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gth_tx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gth_rx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
]


class Platform(XilinxPlatform):
    def __init__(self):
        XilinxPlatform.__init__(self, "", _io)


class GTHSim(Module):
    def __init__(self, platform):
        clk_freq = 100e6
        self.submodules.crg = CRG(platform.request("clk100"))

        refclk = Signal()
        refclk_pads = platform.request("gth_refclk")
        self.specials += [
            Instance("IBUFDS_GTE3",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk)
        ]

        cpll = GTHChannelPLL(refclk, 125e6, 1.25e9)
        print(cpll)
        self.submodules += cpll


        tx_pads = platform.request("gth_tx")
        rx_pads = platform.request("gth_rx")
        gth = GTH(cpll, tx_pads, rx_pads, clk_freq,
            clock_aligner=True, internal_loopback=False)
        self.submodules += gth

        counter = Signal(8)
        self.sync.tx += counter.eq(counter + 1)

        self.comb += [
            gth.encoder.k[0].eq(1),
            gth.encoder.d[0].eq((5 << 5) | 28),
            gth.encoder.k[1].eq(0),
            gth.encoder.d[1].eq(counter),
        ]


def generate_top():
    platform = Platform()
    soc = GTHSim(platform)
    platform.build(soc, build_dir="./", run=False)

def generate_top_tb():
    f = open("top_tb.v", "w")
    f.write("""
`timescale 1ns/1ps

module top_tb();

reg clk100;
initial clk100 = 1'b1;
always #5 clk100 = ~clk100;

reg gth_refclk;
initial gth_refclk = 1'b1;
always #4 gth_refclk = ~gth_refclk;

wire gth_p;
wire gth_n;

top dut (
    .clk100(clk100),
    .gth_refclk_p(gth_refclk),
    .gth_refclk_n(~gth_refclk),
    .gth_tx_p(gth_p),
    .gth_tx_n(gth_n),
    .gth_rx_p(gth_p),
    .gth_rx_n(gth_n)
);

endmodule""")
    f.close()

def run_sim():
    os.system("rm -rf xsim.dir")
    os.system("call xvlog glbl.v")
    os.system("call xvlog top.v")
    os.system("call xvlog top_tb.v")
    os.system("call xelab -debug typical top_tb glbl -s top_tb_sim -L unisims_ver -L unimacro_ver -L SIMPRIM_VER -L secureip -L $xsimdir/xil_defaultlib -timescale 1ns/1ps")
    os.system("call xsim top_tb_sim -gui")

def main():
    generate_top()
    generate_top_tb()
    run_sim()

if __name__ == "__main__":
    main()
