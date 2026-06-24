from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceCandidate:
    title: str
    source_type: str
    authority: str
    url: str
    why_relevant: str
    expected_checks: tuple[str, ...]

    def to_record(self) -> dict:
        return asdict(self)


STM32F103_USB_CDC_SOURCES: tuple[SourceCandidate, ...] = (
    SourceCandidate(
        title="STM32F103x8/B datasheet",
        source_type="official_datasheet",
        authority="STMicroelectronics",
        url="https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
        why_relevant="确认 STM32F103C8T6 USB FS 外设、封装、供电、时钟和 PA11/PA12 引脚事实。",
        expected_checks=(
            "USB 2.0 full-speed device peripheral availability",
            "PA11/PA12 USB_DM/USB_DP alternate function",
            "electrical and clock requirements relevant to USB operation",
        ),
    ),
    SourceCandidate(
        title="RM0008 STM32F10xxx reference manual",
        source_type="reference_manual",
        authority="STMicroelectronics",
        url="https://www.st.com/resource/en/reference_manual/cd00171190.pdf",
        why_relevant="确认 USB FS device 控制器、时钟树、寄存器和中断行为。",
        expected_checks=(
            "USB peripheral clock source and 48 MHz requirement",
            "USB device register behavior and reset/interrupt flow",
            "RCC clock tree constraints for USB",
        ),
    ),
    SourceCandidate(
        title="STM32CubeF1 firmware package",
        source_type="official_sdk",
        authority="STMicroelectronics",
        url="https://github.com/STMicroelectronics/STM32CubeF1",
        why_relevant="确认 HAL PCD、USB Device CDC 中间件和官方示例/模板代码。",
        expected_checks=(
            "HAL_PCD and USB Device library API names",
            "CDC class middleware file layout",
            "example project configuration for USB device where available",
        ),
    ),
    SourceCandidate(
        title="STM32CubeMX user manual",
        source_type="official_tool_manual",
        authority="STMicroelectronics",
        url="https://www.st.com/resource/en/user_manual/um1718-stm32cubemx-for-stm32-configuration-and-initialization-c-code-generation-stmicroelectronics.pdf",
        why_relevant="确认 STM32CubeMX 工程配置、代码生成和命令行/脚本能力边界。",
        expected_checks=(
            "supported project generation workflow",
            "documented command-line or script mode capabilities",
            "limits of semantic .ioc modification through official tooling",
        ),
    ),
)


def source_lookup(intent: str) -> list[SourceCandidate]:
    normalized = intent.lower()
    if "stm32" in normalized and ("usb" in normalized or "cdc" in normalized):
        return list(STM32F103_USB_CDC_SOURCES)
    return []
