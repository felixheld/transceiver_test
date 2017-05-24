from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

master_serdes_rx_bitslip = 0
master_serdes_rx_delay = 0

slave_serdes_rx_bitslip = 18
slave_serdes_rx_delay = 0

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

# analyzer
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
