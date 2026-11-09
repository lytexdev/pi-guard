"""The YOLOX network in plain PyTorch, so official weights load without the YOLOX package.

Follows the architecture of YOLOX by Megvii (https://github.com/Megvii-BaseDetection/YOLOX,
Apache-2.0): CSPDarknet backbone, PAFPN neck and the decoupled head. Module names match the
official checkpoints key for key, and `test_yolox_model.py` checks that this network gives
the same output as the ONNX model the Pi runs.

Output, as on the Pi: (batch, 3549, 85) for 416 pixels, per grid cell (x, y, w, h) raw,
then objectness and 80 class scores after a sigmoid. With `logits=True` the last 81 come
before the sigmoid, for training.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn

BN_EPS, BN_MOMENTUM = 1e-3, 0.03


class BaseConv(nn.Module):
    def __init__(self, cin: int, cout: int, ksize: int, stride: int, groups: int = 1) -> None:
        super().__init__()
        self.conv = nn.Conv2d(cin, cout, ksize, stride, (ksize - 1) // 2, groups=groups,
                              bias=False)  # fmt: skip
        self.bn = nn.BatchNorm2d(cout, eps=BN_EPS, momentum=BN_MOMENTUM)
        self.act = nn.SiLU(inplace=True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.act(self.bn(self.conv(x)))


class DWConv(nn.Module):
    def __init__(self, cin: int, cout: int, ksize: int, stride: int = 1) -> None:
        super().__init__()
        self.dconv = BaseConv(cin, cin, ksize, stride, groups=cin)
        self.pconv = BaseConv(cin, cout, 1, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pconv(self.dconv(x))


def _conv(depthwise: bool) -> type[nn.Module]:
    return DWConv if depthwise else BaseConv


class Bottleneck(nn.Module):
    def __init__(
        self, cin: int, cout: int, shortcut: bool, expansion: float, depthwise: bool
    ) -> None:
        super().__init__()
        hidden = int(cout * expansion)
        self.conv1 = BaseConv(cin, hidden, 1, 1)
        self.conv2 = _conv(depthwise)(hidden, cout, 3, 1)
        self.use_add = shortcut and cin == cout

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        y = self.conv2(self.conv1(x))
        return y + x if self.use_add else y


class SPPBottleneck(nn.Module):
    def __init__(self, cin: int, cout: int, kernels: tuple[int, ...] = (5, 9, 13)) -> None:
        super().__init__()
        hidden = cin // 2
        self.conv1 = BaseConv(cin, hidden, 1, 1)
        self.m = nn.ModuleList(nn.MaxPool2d(k, 1, k // 2) for k in kernels)
        self.conv2 = BaseConv(hidden * (len(kernels) + 1), cout, 1, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv1(x)
        return self.conv2(torch.cat([x, *(m(x) for m in self.m)], 1))


class CSPLayer(nn.Module):
    def __init__(
        self, cin: int, cout: int, n: int, shortcut: bool = True, depthwise: bool = False
    ) -> None:
        super().__init__()
        hidden = int(cout * 0.5)
        self.conv1 = BaseConv(cin, hidden, 1, 1)
        self.conv2 = BaseConv(cin, hidden, 1, 1)
        self.conv3 = BaseConv(2 * hidden, cout, 1, 1)
        self.m = nn.Sequential(
            *(Bottleneck(hidden, hidden, shortcut, 1.0, depthwise) for _ in range(n))
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv3(torch.cat((self.m(self.conv1(x)), self.conv2(x)), 1))


class Focus(nn.Module):
    def __init__(self, cin: int, cout: int, ksize: int) -> None:
        super().__init__()
        self.conv = BaseConv(cin * 4, cout, ksize, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.conv(torch.cat((x[..., ::2, ::2], x[..., 1::2, ::2],
                                    x[..., ::2, 1::2], x[..., 1::2, 1::2]), 1))  # fmt: skip


class CSPDarknet(nn.Module):
    def __init__(self, depth: float, width: float, depthwise: bool) -> None:
        super().__init__()
        conv = _conv(depthwise)
        c, d = int(width * 64), max(round(depth * 3), 1)
        self.stem = Focus(3, c, 3)
        self.dark2 = nn.Sequential(
            conv(c, c * 2, 3, 2), CSPLayer(c * 2, c * 2, d, depthwise=depthwise)
        )
        self.dark3 = nn.Sequential(
            conv(c * 2, c * 4, 3, 2), CSPLayer(c * 4, c * 4, d * 3, depthwise=depthwise)
        )
        self.dark4 = nn.Sequential(
            conv(c * 4, c * 8, 3, 2), CSPLayer(c * 8, c * 8, d * 3, depthwise=depthwise)
        )
        self.dark5 = nn.Sequential(
            conv(c * 8, c * 16, 3, 2),
            SPPBottleneck(c * 16, c * 16),
            CSPLayer(c * 16, c * 16, d, shortcut=False, depthwise=depthwise),
        )

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x3 = self.dark3(self.dark2(self.stem(x)))
        x4 = self.dark4(x3)
        return x3, x4, self.dark5(x4)


class YOLOPAFPN(nn.Module):
    def __init__(self, depth: float, width: float, depthwise: bool) -> None:
        super().__init__()
        conv = _conv(depthwise)
        c0, c1, c2 = (int(c * width) for c in (256, 512, 1024))
        n = round(3 * depth)
        self.backbone = CSPDarknet(depth, width, depthwise)
        self.upsample = nn.Upsample(scale_factor=2, mode="nearest")
        self.lateral_conv0 = BaseConv(c2, c1, 1, 1)
        self.C3_p4 = CSPLayer(2 * c1, c1, n, False, depthwise)
        self.reduce_conv1 = BaseConv(c1, c0, 1, 1)
        self.C3_p3 = CSPLayer(2 * c0, c0, n, False, depthwise)
        self.bu_conv2 = conv(c0, c0, 3, 2)
        self.C3_n3 = CSPLayer(2 * c0, c1, n, False, depthwise)
        self.bu_conv1 = conv(c1, c1, 3, 2)
        self.C3_n4 = CSPLayer(2 * c1, c2, n, False, depthwise)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        x2, x1, x0 = self.backbone(x)
        fpn_out0 = self.lateral_conv0(x0)
        f_out0 = self.C3_p4(torch.cat([self.upsample(fpn_out0), x1], 1))
        fpn_out1 = self.reduce_conv1(f_out0)
        pan_out2 = self.C3_p3(torch.cat([self.upsample(fpn_out1), x2], 1))
        pan_out1 = self.C3_n3(torch.cat([self.bu_conv2(pan_out2), fpn_out1], 1))
        pan_out0 = self.C3_n4(torch.cat([self.bu_conv1(pan_out1), fpn_out0], 1))
        return pan_out2, pan_out1, pan_out0


class YOLOXHead(nn.Module):
    def __init__(self, num_classes: int, width: float, depthwise: bool) -> None:
        super().__init__()
        conv = _conv(depthwise)
        hidden = int(256 * width)
        self.stems, self.cls_convs, self.reg_convs = (
            nn.ModuleList(),
            nn.ModuleList(),
            nn.ModuleList(),
        )
        self.cls_preds, self.reg_preds, self.obj_preds = (
            nn.ModuleList(),
            nn.ModuleList(),
            nn.ModuleList(),
        )
        for channels in (256, 512, 1024):
            self.stems.append(BaseConv(int(channels * width), hidden, 1, 1))
            self.cls_convs.append(
                nn.Sequential(conv(hidden, hidden, 3, 1), conv(hidden, hidden, 3, 1))
            )
            self.reg_convs.append(
                nn.Sequential(conv(hidden, hidden, 3, 1), conv(hidden, hidden, 3, 1))
            )
            self.cls_preds.append(nn.Conv2d(hidden, num_classes, 1))
            self.reg_preds.append(nn.Conv2d(hidden, 4, 1))
            self.obj_preds.append(nn.Conv2d(hidden, 1, 1))

    def forward(self, features: tuple[torch.Tensor, ...], logits: bool = False) -> torch.Tensor:
        outputs = []
        for k, x in enumerate(features):
            x = self.stems[k](x)
            cls = self.cls_preds[k](self.cls_convs[k](x))
            reg_feat = self.reg_convs[k](x)
            reg, obj = self.reg_preds[k](reg_feat), self.obj_preds[k](reg_feat)
            if not logits:
                obj, cls = obj.sigmoid(), cls.sigmoid()
            outputs.append(torch.cat([reg, obj, cls], 1).flatten(start_dim=2))
        return torch.cat(outputs, 2).permute(0, 2, 1)


@dataclass(frozen=True)
class Size:
    depth: float
    width: float
    depthwise: bool


SIZES = {
    "nano": Size(0.33, 0.25, True),
    "tiny": Size(0.33, 0.375, False),
    "s": Size(0.33, 0.50, False),
    "m": Size(0.67, 0.75, False),
    "l": Size(1.0, 1.0, False),
}


class YOLOX(nn.Module):
    def __init__(self, size: str, num_classes: int = 80) -> None:
        super().__init__()
        s = SIZES[size]
        self.backbone = YOLOPAFPN(s.depth, s.width, s.depthwise)
        self.head = YOLOXHead(num_classes, s.width, s.depthwise)

    def forward(self, x: torch.Tensor, logits: bool = False) -> torch.Tensor:
        return self.head(self.backbone(x), logits)


def load(size: str, checkpoint: str, device: str = "cpu") -> YOLOX:
    """A network with official or own weights. `weights_only` refuses anything but tensors,
    so a checkpoint cannot run code."""
    model = YOLOX(size)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state.get("model", state))
    return model.to(device).eval()


class Exported(nn.Module):
    """What goes into the ONNX file: the Pi's input and output, nothing more."""

    def __init__(self, model: YOLOX) -> None:
        super().__init__()
        self.model = model

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        return self.model(images)
