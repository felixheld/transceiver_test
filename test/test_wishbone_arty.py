from litex.soc.tools.remote import RemoteClient
from litescope.software.driver.analyzer import LiteScopeAnalyzerDriver

wb = RemoteClient(debug=False)
wb.open()

# # #

identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*i))
print(identifier)

analyzer = LiteScopeAnalyzerDriver(wb.regs, "analyzer", debug=True)
analyzer.configure_trigger(cond={"slave_etherbone_wishbone_bus_stb": 1,
	                             "slave_etherbone_wishbone_bus_cyc": 1})
analyzer.configure_subsampler(1)
analyzer.run(offset=16, length=128)

wb.write(wb.mems.wbslave.base + 0x0000, 0x12345678)

while not analyzer.done():
	pass

analyzer.upload()
analyzer.save("dump.vcd")

# # #

wb.close()
