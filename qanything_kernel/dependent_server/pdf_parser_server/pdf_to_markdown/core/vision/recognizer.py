import os
from copy import deepcopy
import onnxruntime as ort
import torch
from qanything_kernel.dependent_server.pdf_parser_server.pdf_to_markdown.core.vision.operators import *


class Recognizer(object):
    def __init__(self, label_list, task_name, model_dir=None, device=torch.device("cpu")):
        """
        If you have trouble downloading HuggingFace models, -_^ this might help!!

        For Linux:
        export HF_ENDPOINT=https://hf-mirror.com

        For Windows:
        Good luck
        ^_-

        """
        model_file_path = os.path.join(model_dir, task_name + ".onnx")
        if not os.path.exists(model_file_path):
            raise ValueError("not find model file path {}".format(
                model_file_path))
        # if ort.get_device() == "GPU":
        if device == torch.device("cuda"):
            options = ort.SessionOptions()
            options.enable_cpu_mem_arena = False
            self.ort_sess = ort.InferenceSession(model_file_path, options=options,
                                                 providers=[('CUDAExecutionProvider')])
        else:
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.ort_sess = ort.InferenceSession(model_file_path, sess_options, providers=['CPUExecutionProvider'])
        self.input_names = [node.name for node in self.ort_sess.get_inputs()]
        self.output_names = [node.name for node in self.ort_sess.get_outputs()]
        self.input_shape = self.ort_sess.get_inputs()[0].shape[2:4]
        self.label_list = label_list

    @staticmethod
    def sort_Y_firstly(arr, threashold):
        # sort using y1 first and then x1
        arr = sorted(arr, key=lambda r: (r["top"], r["x0"]))
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                # restore the order using th
                if abs(arr[j + 1]["top"] - arr[j]["top"]) < threashold \
                        and arr[j + 1]["x0"] < arr[j]["x0"]:
                    tmp = deepcopy(arr[j])
                    arr[j] = deepcopy(arr[j + 1])
                    arr[j + 1] = deepcopy(tmp)
        return arr

    @staticmethod
    def sort_X_firstly(arr, threashold, copy=True):
        # sort using y1 first and then x1
        arr = sorted(arr, key=lambda r: (r["x0"], r["top"]))
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                # restore the order using th
                if abs(arr[j + 1]["x0"] - arr[j]["x0"]) < threashold \
                        and arr[j + 1]["top"] < arr[j]["top"]:
                    tmp = deepcopy(arr[j]) if copy else arr[j]
                    arr[j] = deepcopy(arr[j + 1]) if copy else arr[j + 1]
                    arr[j + 1] = deepcopy(tmp) if copy else tmp
        return arr

    @staticmethod
    def sort_C_firstly(arr, thr=0):
        # sort using y1 first and then x1
        # sorted(arr, key=lambda r: (r["x0"], r["top"]))
        arr = Recognizer.sort_X_firstly(arr, thr)
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                # restore the order using th
                if "C" not in arr[j] or "C" not in arr[j + 1]:
                    continue
                if arr[j + 1]["C"] < arr[j]["C"] \
                        or (
                        arr[j + 1]["C"] == arr[j]["C"]
                        and arr[j + 1]["top"] < arr[j]["top"]
                ):
                    tmp = arr[j]
                    arr[j] = arr[j + 1]
                    arr[j + 1] = tmp
        return arr

        return sorted(arr, key=lambda r: (r.get("C", r["x0"]), r["top"]))

    @staticmethod
    def sort_R_firstly(arr, thr=0):
        # sort using y1 first and then x1
        # sorted(arr, key=lambda r: (r["top"], r["x0"]))
        arr = Recognizer.sort_Y_firstly(arr, thr)
        for i in range(len(arr) - 1):
            for j in range(i, -1, -1):
                if "R" not in arr[j] or "R" not in arr[j + 1]:
                    continue
                if arr[j + 1]["R"] < arr[j]["R"] \
                        or (
                        arr[j + 1]["R"] == arr[j]["R"]
                        and arr[j + 1]["x0"] < arr[j]["x0"]
                ):
                    tmp = arr[j]
                    arr[j] = arr[j + 1]
                    arr[j + 1] = tmp
        return arr

    @staticmethod
    def overlapped_area(a, b, ratio=True):
        tp, btm, x0, x1 = a["top"], a["bottom"], a["x0"], a["x1"]
        if b["x0"] > x1 or b["x1"] < x0:
            return 0
        if b["bottom"] < tp or b["top"] > btm:
            return 0
        x0_ = max(b["x0"], x0)
        x1_ = min(b["x1"], x1)
        assert x0_ <= x1_, "Fuckedup! T:{},B:{},X0:{},X1:{} ==> {}".format(
            tp, btm, x0, x1, b)
        tp_ = max(b["top"], tp)
        btm_ = min(b["bottom"], btm)
        assert tp_ <= btm_, "Fuckedup! T:{},B:{},X0:{},X1:{} => {}".format(
            tp, btm, x0, x1, b)
        ov = (btm_ - tp_) * (x1_ - x0_) if x1 - \
                                           x0 != 0 and btm - tp != 0 else 0
        if ov > 0 and ratio:
            ov /= (x1 - x0) * (btm - tp)
        return ov

    @staticmethod
    def layouts_cleanup(boxes, layouts, far=5, thr=0.7):
        def notOverlapped(a, b):
            return any([a["x1"] < b["x0"],
                        a["x0"] > b["x1"],
                        a["bottom"] < b["top"],
                        a["top"] > b["bottom"]])

        i = 0
        while i + 1 < len(layouts):
            j = i + 1
            while j < min(i + far, len(layouts)) \
                    and (layouts[i].get("type", "") != layouts[j].get("type", "")
                         or notOverlapped(layouts[i], layouts[j])):
                j += 1
            if j >= min(i + far, len(layouts)):
                i += 1
                continue
            if Recognizer.overlapped_area(layouts[i], layouts[j]) < thr \
                    and Recognizer.overlapped_area(layouts[j], layouts[i]) < thr:
                i += 1
                continue

            if layouts[i].get("score") and layouts[j].get("score"):
                if layouts[i]["type"] == 'figure' or layouts[i]["type"] == 'equation':
                    if Recognizer.overlapped_area(layouts[j], layouts[i]) > Recognizer.overlapped_area(layouts[i],
                                                                                                       layouts[j]):
                        layouts.pop(j)
                    else:
                        layouts.pop(i)
                else:
                    if layouts[i]["score"] > layouts[j]["score"]:
                        layouts.pop(j)
                    else:
                        layouts.pop(i)
                continue

            area_i, area_i_1 = 0, 0
            for b in boxes:
                if not notOverlapped(b, layouts[i]):
                    area_i += Recognizer.overlapped_area(b, layouts[i], False)
                if not notOverlapped(b, layouts[j]):
                    area_i_1 += Recognizer.overlapped_area(b, layouts[j], False)

            if area_i > area_i_1:
                layouts.pop(j)
            else:
                layouts.pop(i)

        return layouts

    def create_inputs(self, imgs, im_info):
        """generate input for different model type
        Args:
            imgs (list(numpy)): list of images (np.ndarray)
            im_info (list(dict)): list of image info
        Returns:
            inputs (dict): input of model
        """
        inputs = {}

        im_shape = []
        scale_factor = []
        if len(imgs) == 1:
            inputs['image'] = np.array((imgs[0],)).astype('float32')
            inputs['im_shape'] = np.array(
                (im_info[0]['im_shape'],)).astype('float32')
            inputs['scale_factor'] = np.array(
                (im_info[0]['scale_factor'],)).astype('float32')
            return inputs

        for e in im_info:
            im_shape.append(np.array((e['im_shape'],)).astype('float32'))
            scale_factor.append(np.array((e['scale_factor'],)).astype('float32'))

        inputs['im_shape'] = np.concatenate(im_shape, axis=0)
        inputs['scale_factor'] = np.concatenate(scale_factor, axis=0)

        imgs_shape = [[e.shape[1], e.shape[2]] for e in imgs]
        max_shape_h = max([e[0] for e in imgs_shape])
        max_shape_w = max([e[1] for e in imgs_shape])
        padding_imgs = []
        for img in imgs:
            im_c, im_h, im_w = img.shape[:]
            padding_im = np.zeros(
                (im_c, max_shape_h, max_shape_w), dtype=np.float32)
            padding_im[:, :im_h, :im_w] = img
            padding_imgs.append(padding_im)
        inputs['image'] = np.stack(padding_imgs, axis=0)
        return inputs

    @staticmethod
    def find_overlapped(box, boxes_sorted_by_y, naive=False):
        if not boxes_sorted_by_y:
            return
        bxs = boxes_sorted_by_y
        s, e, ii = 0, len(bxs), 0
        while s < e and not naive:
            ii = (e + s) // 2
            pv = bxs[ii]
            if box["bottom"] < pv["top"]:
                e = ii
                continue
            if box["top"] > pv["bottom"]:
                s = ii + 1
                continue
            break
        while s < ii:
            if box["top"] > bxs[s]["bottom"]:
                s += 1
            break
        while e - 1 > ii:
            if box["bottom"] < bxs[e - 1]["top"]:
                e -= 1
            break

        max_overlaped_i, max_overlaped = None, 0
        for i in range(s, e):
            ov = Recognizer.overlapped_area(bxs[i], box)
            if ov <= max_overlaped:
                continue
            max_overlaped_i = i
            max_overlaped = ov

        return max_overlaped_i

    @staticmethod
    def find_horizontally_tightest_fit(box, boxes):
        if not boxes:
            return
        min_dis, min_i = 1000000, None
        for i, b in enumerate(boxes):
            if box.get("layoutno", "0") != b.get("layoutno", "0"): continue
            dis = min(abs(box["x0"] - b["x0"]), abs(box["x1"] - b["x1"]),
                      abs(box["x0"] + box["x1"] - b["x1"] - b["x0"]) / 2)
            if dis < min_dis:
                min_i = i
                min_dis = dis
        return min_i

    @staticmethod
    def find_overlapped_with_threashold(box, boxes, thr=0.3):
        if not boxes:
            return
        max_overlapped_i, max_overlapped, _max_overlapped = None, thr, 0
        s, e = 0, len(boxes)
        for i in range(s, e):
            ov = Recognizer.overlapped_area(box, boxes[i])
            _ov = Recognizer.overlapped_area(boxes[i], box)
            if (ov, _ov) < (max_overlapped, _max_overlapped):
                continue
            max_overlapped_i = i
            max_overlapped = ov
            _max_overlapped = _ov

        return max_overlapped_i

    def preprocess(self, image_list):
        inputs = []
        hh, ww = self.input_shape
        for img in image_list:
            h, w = img.shape[:2]
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            scale = min(hh / h, ww / w)
            img = cv2.resize(np.array(img).astype('float32'), (int(round(scale * w)), int(round(scale * h))))
            dw, dh = hh - img.shape[1], ww - img.shape[0]
            dw /= 2  # divide padding into 2 sides
            dh /= 2
            top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
            left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
            img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114, 114, 114))
            # img = cv2.resize(np.array(img).astype('float32'), (ww, hh))
            # Scale input pixel values to 0 to 1

            input_img = img
            r_h, r_w = input_img.shape[:2]
            img /= 255.0
            img = img.transpose(2, 0, 1)
            img = img[np.newaxis, :, :, :].astype(np.float32)
            inputs.append({self.input_names[0]: img, "scale_factor": [r_h, r_w, h, w]})
        return inputs

    def postprocess(self, boxes, inputs, thr):

        def xywh2xyxy(x):
            # [x, y, w, h] to [x1, y1, x2, y2]
            y = np.copy(x)
            y[:, 0] = x[:, 0] - x[:, 2] / 2
            y[:, 1] = x[:, 1] - x[:, 3] / 2
            y[:, 2] = x[:, 0] + x[:, 2] / 2
            y[:, 3] = x[:, 1] + x[:, 3] / 2
            return y

        def scale_boxes(img1_shape, boxes, img0_shape):
            gain = min(img1_shape[0] / img0_shape[0], img1_shape[1] / img0_shape[1])  # gain  = old / new
            pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
            boxes[..., [0, 2]] -= pad[0]  # x padding
            boxes[..., [1, 3]] -= pad[1]  # y padding
            boxes[..., :4] /= gain
            # clip_boxes(boxes, img0_shape)
            boxes[..., [0, 2]] = boxes[..., [0, 2]].clip(0, img0_shape[1])  # x1, x2
            boxes[..., [1, 3]] = boxes[..., [1, 3]].clip(0, img0_shape[0])
            return boxes

        def compute_iou(box, boxes):
            # Compute xmin, ymin, xmax, ymax for both boxes
            xmin = np.maximum(box[0], boxes[:, 0])
            ymin = np.maximum(box[1], boxes[:, 1])
            xmax = np.minimum(box[2], boxes[:, 2])
            ymax = np.minimum(box[3], boxes[:, 3])

            # Compute intersection area
            intersection_area = np.maximum(0, xmax - xmin) * np.maximum(0, ymax - ymin)

            # Compute union area
            box_area = (box[2] - box[0]) * (box[3] - box[1])
            boxes_area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
            union_area = box_area + boxes_area - intersection_area

            # Compute IoU
            iou = intersection_area / union_area

            return iou

        def iou_filter(boxes, scores, iou_threshold):
            sorted_indices = np.argsort(scores)[::-1]

            keep_boxes = []
            while sorted_indices.size > 0:
                # Pick the last box
                box_id = sorted_indices[0]
                keep_boxes.append(box_id)

                # Compute IoU of the picked box with the rest
                ious = compute_iou(boxes[box_id, :], boxes[sorted_indices[1:], :])

                # Remove boxes with IoU over the threshold
                keep_indices = np.where(ious < iou_threshold)[0]

                # print(keep_indices.shape, sorted_indices.shape)
                sorted_indices = sorted_indices[keep_indices + 1]

            return keep_boxes

        def nms(boxes, scores, iou_threshold):
            # Sort by score
            sorted_indices = np.argsort(scores)[::-1]
            # print(boxes.shape)
            # print(scores.shape)
            keep_boxes = []
            while sorted_indices.size > 0:
                # Pick the last box
                box_id = sorted_indices[0]
                keep_boxes.append(box_id)

                # Compute IoU of the picked box with the rest
                ious = compute_iou(boxes[box_id, :], boxes[sorted_indices[1:], :])

                # Remove boxes with IoU over the threshold
                keep_indices = np.where(ious < iou_threshold)[0]

                # print(keep_indices.shape, sorted_indices.shape)
                sorted_indices = sorted_indices[keep_indices + 1]

            return keep_boxes

        boxes = np.squeeze(boxes).T
        # Filter out object confidence scores below threshold
        scores = np.max(boxes[:, 4:], axis=1)
        boxes = boxes[scores > thr, :]
        scores = scores[scores > thr]
        if len(boxes) == 0: return []
        # Get the class with the highest confidence
        class_ids = np.argmax(boxes[:, 4:], axis=1)
        boxes = boxes[:, :4]
        r_input_shape = np.array([inputs["scale_factor"][0], inputs["scale_factor"][1]])
        o_input_shape = np.array([inputs["scale_factor"][2], inputs["scale_factor"][3]])
        # boxes = np.multiply(boxes, input_shape, dtype=np.float32)
        boxes = xywh2xyxy(boxes)
        boxes = scale_boxes(r_input_shape, boxes, o_input_shape)
        indices = nms(boxes, scores, 0.65)
        return [{
            "type": self.label_list[class_ids[i]].lower(),
            "bbox": [float(t) for t in boxes[i].tolist()],
            "score": float(scores[i])
        } for i in indices]

    def __call__(self, image_list, thr=0.4, batch_size=16):
        res = []
        imgs = []
        for i in range(len(image_list)):
            if not isinstance(image_list[i], np.ndarray):
                imgs.append(np.array(image_list[i]))
            else:
                imgs.append(image_list[i])

        batch_loop_cnt = math.ceil(float(len(imgs)) / batch_size)
        for i in range(batch_loop_cnt):
            start_index = i * batch_size
            end_index = min((i + 1) * batch_size, len(imgs))
            batch_image_list = imgs[start_index:end_index]
            inputs = self.preprocess(batch_image_list)
            print("preprocess")
            for ins in inputs:
                bb = self.postprocess(
                    self.ort_sess.run(None, {k: v for k, v in ins.items() if k in self.input_names})[0], ins, thr)
                # print(f"page_rec_res: {bb}")
                res.append(bb)

        return res
