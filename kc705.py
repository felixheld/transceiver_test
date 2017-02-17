#!/usr/bin/env python3

from litex.gen import *
from litex.boards.platforms import kc705

from litex.gen.genlib.io import CRG

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart.bridge import UARTWishboneBridge

from litex.build.generic_platform import *
from litex.boards.platforms import kc705

from gtx_7series import GTX_1000BASE_BX10


_extension_io = [
    ("refclk125", 0,
        Subsignal("p", Pins("G8")),
        Subsignal("n", Pins("G7")),
    ),
    ("sma_tx", 0,
        Subsignal("p", Pins("K2")),
        Subsignal("n", Pins("K1")),
    ),
    ("sma_rx", 0,
        Subsignal("p", Pins("K6")),
        Subsignal("n", Pins("K5")),
    ),
    ("sfp_tx", 0,
        Subsignal("p", Pins("H2")),
        Subsignal("n", Pins("H1")),
    ),
    ("sfp_rx", 0,
        Subsignal("p", Pins("G4")),
        Subsignal("n", Pins("G3")),
    ),
]


class Platform(kc705.Platform):
    def __init__(self, *args, **kwargs):
        kc705.Platform.__init__(self, *args, **kwargs)
        self.add_extension(_extension_io)
        self.add_platform_command("""
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 2.5 [current_design]
""")


class BaseSoC(SoCCore):
    def __init__(self, platform):
        clk_freq = int(1e9/platform.default_clk_period)
        SoCCore.__init__(self, platform, clk_freq,
            cpu_type=None,
            csr_data_width=32,
            with_uart=False,
            ident="DRTIO GTX KC705 Test Design",
            with_timer=False
        )
        self.submodules.crg = CRG(platform.request(platform.default_clk_name))
        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"),
                                                  clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        gtx = GTX_1000BASE_BX10(platform.request("refclk125"),
                                        platform.request("sfp_tx"),
                                        platform.request("sfp_rx"),
                                        clk_freq,
                                        clock_div2=True)
        counter = Signal(32)
        self.sync += counter.eq(counter + 1)
        self.submodules += gtx
        self.comb += [
            gtx.encoder.k[0].eq(1),
            gtx.encoder.d[0].eq((5 << 5) | 28),
            gtx.encoder.k[1].eq(0),
            gtx.encoder.d[1].eq(counter[26:]),
        ]
        self.comb += platform.request("user_led", 4).eq(gtx.rx_ready)
        for i in range(4):
            self.comb += platform.request("user_led", i).eq(gtx.decoders[1].d[i])


def main():
    platform = Platform()
    soc = BaseSoC(platform)
    builder = Builder(soc, output_dir="build", csr_csv="test/csr.csv")
    builder.build()

if __name__ == "__main__":
    main()
