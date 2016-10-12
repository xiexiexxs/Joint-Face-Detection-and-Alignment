import math
import cv2
import caffe
import numpy as np
from jfda.utils import crop_face


class JfdaDetector:
  '''JfdaDetector
  '''

  def __init__(self, nets):
    assert len(nets) in [2, 4, 6], 'wrong number of nets'
    self.pnet, self.rnet, self.onet = None, None, None
    if len(nets) >= 2:
      self.pnet = caffe.Net(nets[0], caffe.TEST, weights=nets[1])
    if len(nets) >= 4:
      self.rnet = caffe.Net(nets[2], caffe.TEST, weights=nets[3])
    if len(nets) >= 6:
      self.onet = caffe.Net(nets[4], caffe.TEST, weights=nets[5])

  def detect(self, img, ths, min_size, factor):
    '''detect face, return bboxes, [bbox score offset landmark]
    '''
    base = 12. / min_size
    height, width = img.shape[:-1]
    l = min(width, height)
    l *= base
    scales = []
    while l > 12:
      scales.append(base)
      base *= factor
      l *= factor
    # stage-1
    bboxes = np.zeros((0, 4 + 1 + 4 + 10), dtype=np.float32)
    for scale in scales:
      w, h = int(math.ceil(scale * width)), int(math.ceil(scale * height))
      data = cv2.resize(img, (w, h))
      data = data.transpose((2, 0, 1)).astype(np.float32)
      data = (data - 128) / 128
      data = data.reshape((1, 3, h, w))
      prob, bbox_pred, landmark_pred = self._forward(self.pnet, data, ['prob', 'bbox_pred', 'landmark_pred'])
      _bboxes = self._gen_bbox(prob[0][1], bbox_pred[0], landmark_pred[0], scale, ths[0])
      keep = nms(_bboxes, 0.5)
      _bboxes = _bboxes[keep]
      bboxes = np.vstack([bboxes, _bboxes])
    keep = nms(bboxes, 0.7)
    bboxes = bboxes[keep]
    bboxes = self._bbox_reg(bboxes)
    bboxes = self._make_square(bboxes)
    # stage-2
    if self.rnet is None or len(bboxes) == 0:
      return bboxes
    n = len(bboxes)
    data = np.zeros((n, 3, 24, 24), dtype=np.float32)
    for i, bbox in enumerate(bboxes):
      face = crop_face(img, bbox[:4])
      data[i] = cv2.resize(face, (24, 24)).transpose((2, 0, 1))
    data = (data - 128) / 128
    prob, bbox_pred, landmark_pred = self._forward(self.rnet, data, ['prob', 'bbox_pred', 'landmark_pred'])
    keep = prob[:, 1] > ths[1]
    bboxes = bboxes[keep]
    bboxes[:, 4] = prob[keep, 1]
    bboxes[:, 5:9] = bbox_pred[keep]
    bboxes[:, 9:] = landmark_pred[keep]
    keep = nms(bboxes, 0.7)
    bboxes = bboxes[keep]
    bboxes = self._bbox_reg(bboxes)
    bboxes = self._make_square(bboxes)
    # stage-3
    if self.onet is None or len(bboxes) == 0:
      return bboxes
    n = len(bboxes)
    data = np.zeros((n, 3, 48, 48), dtype=np.float32)
    for i, bbox in enumerate(bboxes):
      face = crop_face(img, bbox[:4])
      data[i] = cv2.resize(face, (48, 48)).transpose((2, 0, 1))
    data = (data - 128) / 128
    prob, bbox_pred, landmark_pred = self._forward(self.onet, data, ['prob', 'bbox_pred', 'landmark_pred'])
    keep = prob[:, 1] > ths[2]
    bboxes = bboxes[keep]
    bboxes[:, 4] = prob[keep, 1]
    bboxes[:, 5:9] = bbox_pred[keep]
    bboxes[:, 9:] = landmark_pred[keep]
    keep = nms(bboxes, 0.7, 'Min')
    bboxes = bboxes[keep]
    bboxes = self._locate_landmark(bboxes)
    bboxes = self._bbox_reg(bboxes)
    return bboxes

  def _forward(self, net, data, outs):
    '''forward a net with given data, return blobs[out]
    '''
    net.blobs['data'].reshape(*data.shape)
    net.blobs['data'].data[...] = data
    net.forward()
    return [net.blobs[out].data for out in outs]

  def _gen_bbox(self, hotmap, offset, landmark, scale, th):
    '''[x1, y1, x2, y2, score, offset_x1, offset_y1, offset_x2, offset_y2]
    '''
    h, w = hotmap.shape
    stride = 2
    win_size = 12
    hotmap = hotmap.reshape((h, w))
    keep = hotmap > th
    pos = np.where(keep)
    score = hotmap[keep]
    offset = offset[:, keep]
    landmark = landmark[:, keep]
    x, y = pos[1], pos[0]
    x1 = stride * x
    y1 = stride * y
    x2 = x1 + win_size
    y2 = y1 + win_size
    x1 = x1 / scale
    y1 = y1 / scale
    x2 = x2 / scale
    y2 = y2 / scale
    bbox = np.vstack([x1, y1, x2, y2, score, offset, landmark]).transpose()
    return bbox.astype(np.float32)

  def _locate_landmark(self, bboxes):
    w = bboxes[:, 2] - bboxes[:, 0]
    h = bboxes[:, 3] - bboxes[:, 1]
    bboxes[:, 9::2] = bboxes[:, 9::2] * w.reshape((-1, 1)) + bboxes[:, 0].reshape((-1, 1))
    bboxes[:, 10::2] = bboxes[:, 10::2] * h.reshape((-1, 1)) + bboxes[:, 1].reshape((-1, 1))
    return bboxes

  def _bbox_reg(self, bboxes):
    w = bboxes[:, 2] - bboxes[:, 0]
    h = bboxes[:, 3] - bboxes[:, 1]
    bboxes[:, 0] += bboxes[:, 5] * w
    bboxes[:, 1] += bboxes[:, 6] * h
    bboxes[:, 2] += bboxes[:, 7] * w
    bboxes[:, 3] += bboxes[:, 8] * h
    return bboxes

  def _make_square(self, bboxes):
    '''make bboxes sqaure
    '''
    x_center = (bboxes[:, 0] + bboxes[:, 2]) / 2
    y_center = (bboxes[:, 1] + bboxes[:, 3]) / 2
    w = bboxes[:, 2] - bboxes[:, 0]
    h = bboxes[:, 3] - bboxes[:, 1]
    size = np.vstack([w, h]).max(axis=0).transpose()
    bboxes[:, 0] = x_center - size / 2
    bboxes[:, 2] = x_center + size / 2
    bboxes[:, 1] = y_center - size / 2
    bboxes[:, 3] = y_center + size / 2
    return bboxes


def nms(dets, thresh, meth='Union'):
  '''nms from py-faster-rcnn
  '''
  x1 = dets[:, 0]
  y1 = dets[:, 1]
  x2 = dets[:, 2]
  y2 = dets[:, 3]
  scores = dets[:, 4]

  areas = (x2 - x1 + 1) * (y2 - y1 + 1)
  order = scores.argsort()[::-1]

  keep = []
  while order.size > 0:
    i = order[0]
    keep.append(i)
    xx1 = np.maximum(x1[i], x1[order[1:]])
    yy1 = np.maximum(y1[i], y1[order[1:]])
    xx2 = np.minimum(x2[i], x2[order[1:]])
    yy2 = np.minimum(y2[i], y2[order[1:]])

    w = np.maximum(0.0, xx2 - xx1 + 1)
    h = np.maximum(0.0, yy2 - yy1 + 1)
    inter = w * h
    if meth == 'Union':
      ovr = inter / (areas[i] + areas[order[1:]] - inter)
    else:
      ovr = inter / np.minimum(areas[i], areas[order[1:]])

    inds = np.where(ovr <= thresh)[0]
    order = order[inds + 1]

  return keep