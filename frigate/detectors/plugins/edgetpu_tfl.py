import logging
import numpy as np

from frigate.detectors.detection_api import DetectionApi
from frigate.detectors.detector_config import BaseDetectorConfig
from typing import Literal
from pydantic import Extra, Field
import tflite_runtime.interpreter as tflite
from tflite_runtime.interpreter import load_delegate


logger = logging.getLogger(__name__)

DETECTOR_KEY = "edgetpu"


class EdgeTpuDetectorConfig(BaseDetectorConfig):
    type: Literal[DETECTOR_KEY]
    device: str = Field(default=None, title="Device Type")


class EdgeTpuTfl(DetectionApi):
    type_key = DETECTOR_KEY

    def __init__(self, detector_config: EdgeTpuDetectorConfig):
        self.is_audio = detector_config.model.type == "audio"
        device_config = {"device": "usb"}
        if detector_config.device is not None:
            device_config = {"device": detector_config.device}

        edge_tpu_delegate = None

        try:
            logger.info(f"Attempting to load TPU as {device_config['device']}")
            edge_tpu_delegate = load_delegate("libedgetpu.so.1.0", device_config)
            logger.info("TPU found")
            default_model = (
                "/edgetpu_model.tflite"
                if not self.is_audio
                else "/edgetpu_audio_model.tflite"
            )
            self.interpreter = tflite.Interpreter(
                model_path=detector_config.model.path or default_model,
                experimental_delegates=[edge_tpu_delegate],
            )
        except ValueError:
            logger.error(
                "No EdgeTPU was detected. If you do not have a Coral device yet, you must configure CPU detectors."
            )
            raise

        self.interpreter.allocate_tensors()

        self.tensor_input_details = self.interpreter.get_input_details()
        self.tensor_output_details = self.interpreter.get_output_details()

    def detect_raw(self, tensor_input):
        self.interpreter.set_tensor(self.tensor_input_details[0]["index"], tensor_input)
        self.interpreter.invoke()

        detections = np.zeros((20, 6), np.float32)

        if self.is_audio:
            res = self.interpreter.get_tensor(self.tensor_output_details[0]["index"])[0]
            non_zero_indices = res > 0
            class_ids = np.argpartition(-res, 20)[:20]
            class_ids = class_ids[np.argsort(-res[class_ids])]
            class_ids = class_ids[non_zero_indices[class_ids]]
            scores = res[class_ids]
            boxes = np.full((scores.shape[0], 4), -1, np.float32)
            count = len(scores)
        else:
            boxes = self.interpreter.tensor(self.tensor_output_details[0]["index"])()[0]
            class_ids = self.interpreter.tensor(
                self.tensor_output_details[1]["index"]
            )()[0]
            scores = self.interpreter.tensor(self.tensor_output_details[2]["index"])()[
                0
            ]
            count = int(
                self.interpreter.tensor(self.tensor_output_details[3]["index"])()[0]
            )

        for i in range(count):
            if scores[i] < 0.4 or i == 20:
                break
            detections[i] = [
                class_ids[i],
                float(scores[i]),
                boxes[i][0],
                boxes[i][1],
                boxes[i][2],
                boxes[i][3],
            ]

        return detections
