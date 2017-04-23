from litex.soc.tools.remote import RemoteClient

wb = RemoteClient()
wb.open()

# # #

identifier = ""
for i in range(30):
    identifier += chr(wb.read(wb.bases.identifier_mem + 4*i))
print(identifier)

from litescope.software.driver.analyzer import LiteScopeAnalyzerDriver
analyzer = LiteScopeAnalyzerDriver(wb.regs, "analyzer", debug=True)
analyzer.configure_trigger(cond={})
analyzer.configure_subsampler(1)
analyzer.run(offset=128, length=1024)
while not analyzer.done():
    pass
analyzer.upload()
analyzer.save("dump.vcd")

# # #

wb.close()