# 
# LSST Data Management System
# Copyright 2008, 2009, 2010 LSST Corporation.
# 
# This product includes software developed by the
# LSST Project (http://www.lsst.org/).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the LSST License Statement and 
# the GNU General Public License along with this program.  If not, 
# see <http://www.lsstcorp.org/LegalNotices/>.
#

import os

import lsst.utils
import lsst.afw.image.utils as afwImageUtils
import lsst.afw.image as afwImage
from lsst.daf.butlerUtils import CameraMapper
import lsst.pex.policy as pexPolicy
from .monocam import Monocam
from .hack import getDatabase, fakeWcs

__all__ = ["MonocamMapper"]

class MonocamMapper(CameraMapper):
    packageName = 'obs_monocam'

    def __init__(self, inputPolicy=None, **kwargs):
        policyFile = pexPolicy.DefaultPolicyFile(self.packageName, "monocamMapper.paf", "policy")
        policy = pexPolicy.Policy(policyFile)

        # Not sure about this, cargo culted
        self.doFootprints = False
        if inputPolicy is not None:
            for kw in inputPolicy.paramNames(True):
                if kw == "doFootprints":
                    self.doFootprints = True
                else:
                    kwargs[kw] = inputPolicy.get(kw)

        CameraMapper.__init__(self, policy, policyFile.getRepositoryPath(), **kwargs)

        getDatabase(kwargs["root"])

        # Ensure each dataset type of interest knows about the full range of keys available from the registry
        keys = {'visit': int,
                'ccd': int,
                'filter': str,
                'date': str,
                'expTime': float,
                'object': str,
        }
        for name in ("raw", "raw_amp",
                     # processCcd outputs
                     "postISRCCD", "calexp", "postISRCCD", "src", "icSrc", "srcMatch",
                     ):
            self.mappings[name].keyDict.update(keys)

        # @merlin, you should swap these out for the filters you actually intend to use.
        self.filterIdMap = {
                'u': 0, 'g': 1, 'r': 2, 'i': 3, 'z': 4, 'y': 5, 'i2': 5}

        # The LSST Filters from L. Jones 04/07/10
        afwImageUtils.defineFilter('u', 364.59)
        afwImageUtils.defineFilter('g', 476.31, alias=["SDSSG"])
        afwImageUtils.defineFilter('r', 619.42, alias=["SDSSR"])
        afwImageUtils.defineFilter('i', 752.06, alias=["SDSSI"])
        afwImageUtils.defineFilter('z', 866.85, alias=["SDSSZ"])
        afwImageUtils.defineFilter('y', 971.68, alias=['y4']) # official y filter
        afwImageUtils.defineFilter('NONE', 0.0, alias=['no_filter', "OPEN"])

    def _extractDetectorName(self, dataId):
        return "0"

    def _computeCcdExposureId(self, dataId):
        """Compute the 64-bit (long) identifier for a CCD exposure.

        @param dataId (dict) Data identifier with visit
        """
        visit = dataId['visit']
        return long(visit)

    def bypass_ccdExposureId(self, datasetType, pythonType, location, dataId):
        return self._computeCcdExposureId(dataId)

    def bypass_ccdExposureId_bits(self, datasetType, pythonType, location, dataId):
        return 41

    def validate(self, dataId):
        visit = dataId.get("visit")
        if visit is not None and not isinstance(visit, int):
            dataId["visit"] = int(visit)
        return dataId

    def _setCcdExposureId(self, propertyList, dataId):
        propertyList.set("Computed_ccdExposureId", self._computeCcdExposureId(dataId))
        return propertyList

    def _makeCamera(self, policy, repositoryDir):
        """Make a camera (instance of lsst.afw.cameraGeom.Camera) describing the camera geometry
        """
        return Monocam()

    def bypass_defects(self, datasetType, pythonType, location, dataId):
        """ since we have no defects, return an empty list.  Fix this when defects exist """
        return []

    def _defectLookup(self, dataId):
        # Evidently this gets called first
        return "hack"

    def bypass_raw(self, datasetType, pythonType, location, dataId):
        """Read raw image with hacked metadata"""
        filename = location.getLocations()[0]
        md = self.bypass_raw_md(datasetType, pythonType, location, dataId)
        image = afwImage.DecoratedImageU(filename)
        image.setMetadata(md)
        return self.std_raw(image, dataId)

    def bypass_raw_md(self, datasetType, pythonType, location, dataId):
        """Read metadata for raw image, adding fake Wcs"""
        filename = location.getLocations()[0]
        md = afwImage.readMetadata(filename, 1)  # 1 = PHU
        wcs = fakeWcs(md).getFitsMetadata()
        for key in wcs.names():
            md.set(key, wcs.get(key))
        return md

    bypass_raw_amp = bypass_raw
    bypass_raw_amp_md = bypass_raw_md


    def standardizeCalib(self, dataset, item, dataId):
        """Standardize a calibration image read in by the butler

        Some calibrations are stored on disk as Images instead of MaskedImages
        or Exposures.  Here, we convert it to an Exposure.

        @param dataset  Dataset type (e.g., "bias", "dark" or "flat")
        @param item  The item read by the butler
        @param dataId  The data identifier (unused, included for future flexibility)
        @return standardized Exposure
        """
        mapping = self.calibrations[dataset]
        if "MaskedImage" in mapping.python:
            exp = afwImage.makeExposure(item)
        elif "Image" in mapping.python:
            if hasattr(item, "getImage"): # For DecoratedImageX
                item = item.getImage()
            exp = afwImage.makeExposure(afwImage.makeMaskedImage(item))
        elif "Exposure" in mapping.python:
            exp = item
        else:
            raise RuntimeError("Unrecognised python type: %s" % mapping.python)

        if hasattr(CameraMapper, "std_" + dataset):
            return getattr(parent, "std_" + dataset)(self, exp, dataId)
        return self._standardizeExposure(mapping, exp, dataId)

    def std_bias(self, item, dataId):
        return self.standardizeCalib("bias", item, dataId)

    def std_dark(self, item, dataId):
        exp = self.standardizeCalib("dark", item, dataId)
        exp.getCalib().setExptime(1.0)
        return exp

    def std_flat(self, item, dataId):
        return self.standardizeCalib("flat", item, dataId)

    def std_fringe(self, item, dataId):
        return self.standardizeCalib("flat", item, dataId)
