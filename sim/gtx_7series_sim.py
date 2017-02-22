#!/usr/bin/env python3

import os
import sys
sys.path.append("../")

from litex.gen import *
from litex.build.generic_platform import *
from litex.build.xilinx import XilinxPlatform

from litex.gen.genlib.io import CRG

from transceiver.gtx_7series import GTXChannelPLL, GTX


_io = [
    ("clk100", 0, Pins("X")),
    ("gtx_refclk", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gtx_tx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
    ("gtx_rx", 0,
        Subsignal("p", Pins("X")),
        Subsignal("n", Pins("X"))
    ),
]


class Platform(XilinxPlatform):
    def __init__(self):
        XilinxPlatform.__init__(self, "", _io)


class GTXSim(Module):
    def __init__(self, platform):
        clk_freq = 100e6
        self.submodules.crg = CRG(platform.request("clk100"))

        refclk = Signal()
        refclk_pads = platform.request("gtx_refclk")
        self.specials += [
            Instance("IBUFDS_GTE2",
                i_CEB=0,
                i_I=refclk_pads.p,
                i_IB=refclk_pads.n,
                o_O=refclk)
        ]

        cpll = GTXChannelPLL(refclk, 125e6, 2.5e9)
        print(cpll)
        self.submodules += cpll


        tx_pads = platform.request("gtx_tx")
        rx_pads = platform.request("gtx_rx")
        gtx = GTX(cpll, tx_pads, rx_pads, clk_freq, clock_aligner=False)
        self.submodules += gtx

        counter = Signal(8)
        self.sync.rtio += counter.eq(counter + 1)

        self.comb += [
            gtx.encoder.k[0].eq(1),
            gtx.encoder.d[0].eq((5 << 5) | 28),
            gtx.encoder.k[1].eq(0),
            gtx.encoder.d[1].eq(counter),
        ]


def generate_top():
    platform = Platform()
    soc = GTXSim(platform)
    platform.build(soc, build_dir="./", run=False)

def generate_top_tb():
    f = open("top_tb.v", "w")
    f.write("""
`timescale 1ns/1ps

module top_tb();

reg clk100;
initial clk100 = 1'b1;
always #5 clk100 = ~clk100;

reg gtx_refclk;
initial gtx_refclk = 1'b1;
always #4 gtx_refclk = ~gtx_refclk;

wire gtx_p;
wire gtx_n;

top dut (
    .clk100(clk100),
    .gtx_refclk_p(gtx_refclk),
    .gtx_refclk_n(~gtx_refclk),
    .gtx_tx_p(gtx_p),
    .gtx_tx_n(gtx_n),
    .gtx_rx_p(gtx_p),
    .gtx_rx_n(gtx_n)
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
