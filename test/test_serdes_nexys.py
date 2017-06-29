#!/usr/bin/env python3

import time

from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

master_serdes_tx_produce_square_wave = 0
master_serdes_rx_bitslip = 15
master_serdes_rx_delay = 2

slave_serdes_tx_produce_square_wave = 0
slave_serdes_rx_bitslip = 2
slave_serdes_rx_delay = 1

prbs_test = True
prbs_pattern = 0b11
prbs_loop = True

analyzer_test = False

# # #

# get identifier
identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*i))
print(identifier)


# configure master
wb.regs.master_serdes_tx_produce_square_wave.write(master_serdes_tx_produce_square_wave)
wb.regs.master_serdes_rx_bitslip_value.write(master_serdes_rx_bitslip)
wb.regs.master_serdes_rx_delay_rst.write(1)
for i in range(master_serdes_rx_delay):
    wb.regs.master_serdes_rx_delay_inc.write(1)
    wb.regs.master_serdes_rx_delay_ce.write(1)


# configure slave
wb.regs.slave_serdes_tx_produce_square_wave.write(slave_serdes_tx_produce_square_wave)
wb.regs.slave_serdes_rx_bitslip_value.write(slave_serdes_rx_bitslip)
wb.regs.slave_serdes_rx_delay_rst.write(1)
for i in range(slave_serdes_rx_delay):
    wb.regs.slave_serdes_rx_delay_inc.write(1)
    wb.regs.slave_serdes_rx_delay_ce.write(1)


# prbs
wb.regs.master_serdes_phase_detector_reset.write(1)
wb.regs.master_serdes_tx_prbs_config.write(0)
wb.regs.master_serdes_rx_prbs_config.write(0)
wb.regs.slave_serdes_phase_detector_reset.write(1)
wb.regs.slave_serdes_tx_prbs_config.write(0)
wb.regs.slave_serdes_rx_prbs_config.write(0)
if prbs_test:
    wb.regs.master_serdes_tx_prbs_config.write(prbs_pattern)
    wb.regs.slave_serdes_tx_prbs_config.write(prbs_pattern)
    wb.regs.master_serdes_rx_prbs_config.write(prbs_pattern)
    wb.regs.slave_serdes_rx_prbs_config.write(prbs_pattern)
    if prbs_loop:
        print("prbs errors:")
        while True:
            m2s_errors = wb.regs.slave_serdes_rx_prbs_errors.read()
            m2s_phase_detector_status = wb.regs.slave_serdes_phase_detector_status.read()
            s2m_errors = wb.regs.master_serdes_rx_prbs_errors.read()
            s2m_phase_detector_status = wb.regs.master_serdes_phase_detector_status.read()
            print("m2s: {} s:{:2b}/ s2m: {} s:{:2b}".format(
                m2s_errors,
                m2s_phase_detector_status,
                s2m_errors,
                s2m_phase_detector_status))
            time.sleep(1)


# analyzer
if analyzer_test:
    from litescope.software.driver.analyzer import LiteScopeAnalyzerDriver
    analyzer = LiteScopeAnalyzerDriver(wb.regs, "analyzer", debug=True)
    analyzer.configure_trigger(cond={})
    analyzer.configure_subsampler(1)
    analyzer.run(offset=16, length=64)
    analyzer.wait_done()
    analyzer.upload()
    analyzer.save("dump.vcd")

# # #

wb.close()
