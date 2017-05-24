from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

master_serdes_rx_bitslip = 10
master_serdes_rx_delay = 2

slave_serdes_rx_bitslip = 10
slave_serdes_rx_delay = 2

# # #

# get identifier
identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*i))
print(identifier)


# configure master
wb.regs.master_serdes_control_rx_bitslip_value.write(10)
wb.regs.master_serdes_control_rx_delay_rst.write(1)
for i in range(2):
    wb.regs.master_serdes_control_rx_delay_inc.write(1)
    wb.regs.master_serdes_control_rx_delay_ce.write(1)

# configure slave
wb.regs.master_serdes_control_rx_bitslip_value.write(10)
wb.regs.master_serdes_control_rx_delay_rst.write(1)
for i in range(2):
    wb.regs.master_serdes_control_rx_delay_inc.write(1)
    wb.regs.master_serdes_control_rx_delay_ce.write(1)

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
