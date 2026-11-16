"""Machine-specific tool paths for the spike (app zone; stdlib only)."""

from pathlib import Path

QEMU = Path(r"C:\Espressif\tools\tools\qemu-xtensa\esp_develop_9.2.2_20260417\qemu\bin\qemu-system-xtensa.exe")
TOOLBIN = Path(r"C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin")
GDB = Path(r"C:\Espressif\tools\xtensa-esp-elf-gdb\17.1_20260402\xtensa-esp-elf-gdb\bin\xtensa-esp32-elf-gdb.exe")
ROM_ELF = {
    "esp32": Path(r"C:\Espressif\tools\esp-rom-elfs\20241011\esp32_rev300_rom.elf"),
    "esp32s3": Path(r"C:\Espressif\tools\esp-rom-elfs\20241011\esp32s3_rev0_rom.elf"),
}

HERE = Path(__file__).resolve().parent
STEP = HERE.parent  # spike/03-trace
SPIKE = STEP.parent  # spike/

# Ports reserved for the trace worker: 5560-5569.
PORT_UART = 5560
PORT_GDB = 5561
PORT_MON = 5562


def tool(chip: str, name: str) -> Path:
    """xtensa-<chip>-elf-<name>.exe, e.g. tool('esp32', 'objdump')."""
    return TOOLBIN / f"xtensa-{chip}-elf-{name}.exe"
