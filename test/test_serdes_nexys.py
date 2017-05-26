import time

from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

master_serdes_rx_bitslip = 16
master_serdes_rx_delay = 1

slave_serdes_rx_bitslip = 13
slave_serdes_rx_delay = 1

prbs_test = False
prbs_pattern = 0b01
prbs_loop = True

analyzer_test = True

# # #

# get identifier
identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*i))
print(identifier)


# configure master
wb.regs.master_serdes_control_rx_bitslip_value.write(master_serdes_rx_bitslip)
wb.regs.master_serdes_control_rx_delay_rst.write(1)
for i in range(master_serdes_rx_delay):
    wb.regs.master_serdes_control_rx_delay_inc.write(1)
    wb.regs.master_serdes_control_rx_delay_ce.write(1)


# configure slave
wb.regs.slave_serdes_control_rx_bitslip_value.write(slave_serdes_rx_bitslip)
wb.regs.slave_serdes_control_rx_delay_rst.write(1)
for i in range(slave_serdes_rx_delay):
    wb.regs.slave_serdes_control_rx_delay_inc.write(1)
    wb.regs.slave_serdes_control_rx_delay_ce.write(1)


# prbs
wb.regs.master_serdes_control_tx_prbs_config.write(0)
wb.regs.master_serdes_control_rx_prbs_config.write(0)
wb.regs.slave_serdes_control_tx_prbs_config.write(0)
wb.regs.slave_serdes_control_rx_prbs_config.write(0)
if prbs_test:
    wb.regs.master_serdes_control_tx_prbs_config.write(prbs_pattern)
    wb.regs.slave_serdes_control_tx_prbs_config.write(prbs_pattern)
    wb.regs.master_serdes_control_rx_prbs_config.write(prbs_pattern)
    wb.regs.slave_serdes_control_rx_prbs_config.write(prbs_pattern)
    if prbs_loop:
        print("prbs errors:")
        while True:
            m2s_errors = wb.regs.slave_serdes_control_rx_prbs_errors.read()
            s2m_errors = wb.regs.master_serdes_control_rx_prbs_errors.read()
            print("m2s: {}/ s2m: {}".format(m2s_errors, s2m_errors))
            time.sleep(1)


# analyzer
if analyzer_test:
	from litescope.software.driver.analyzer import LiteScopeAnalyzerDriver
	analyzer = LiteScopeAnalyzerDriver(wb.regs, "analyzer", debug=True)
	analyzer.configure_trigger(cond={})
	analyzer.configure_subsampler(1)
	analyzer.run(offset=16, length=64)
	while not analyzer.done():
	    pass
	analyzer.upload()
	analyzer.save("dump.vcd")

# # #

wb.close()
