"""Classes for calibrating a redMaGiC parameter file."""

from __future__ import division, absolute_import, print_function
from past.builtins import xrange

from collections import OrderedDict
import os
import numpy as np
import fitsio
import time
import scipy.optimize
import esutil
import healpy as hp
import copy

from ..configuration import Configuration
from ..fitters import MedZFitter
from ..redsequence import RedSequenceColorPar
from ..galaxy import GalaxyCatalog
from ..catalog import Catalog, Entry
from ..utilities import make_nodes, CubicSpline, interpol, read_members
from ..plotting import SpecPlot, NzPlot
from ..volumelimit import VolumeLimitMask, VolumeLimitMaskFixed
from .redmagic_selector import RedmagicSelector
from .redmagictask import RunRedmagicTask

class RedmagicParameterFitter(object):
    """
    Class for fitting redMaGiC parameters.
    """
    def __init__(self, nodes, corrnodes, z, z_err,
                 chisq, mstar, zcal, zcal_err, refmag,
                 randomn, zmax, etamin, n0,
                 volume, zrange, zbinsize, zredstr, maxchi=20.0, ab_use=None):
        """
        Instantiate a RedmagicParameterFitter

        Parameters
        ----------
        nodes: `np.array`
           Float array of redshift spline nodes for chi2(z)
        corrnodes: `np.array`
           Float array of redshift spline nodes for afterburner correction
        z: `np.array`
           Float array of galaxy (raw) redshifts
        z_err: `np.array`
           Float array of galaxy (raw) redshift errors
        chisq: `np.array`
           Float array of galaxy chisq values
        mstar: `np.array`
           Float array of galaxy mstar values
        zcal: `np.array`
           Float array of calibration redshifts for afterburner
        zcal_err: `np.array`
           Float array of calibration redshift errors (unused)
        refmag: `np.array`
           Float array of galaxy refmags
        randomn: `np.array`
           Float array of normal random draws for sampling
        zmax: `np.array`
           Float array of maximum redshifts for each galaxy position
        etamin: `float`
           Target luminosity cut
        n0: `float`
           Target density
        volume: `np.array`
           Float array of volumes at histogram redshift bins
        zrange: `np.array`
           Two-element float array of redshift range
        zbinsize: `float`
           Redshift histogram bin size
        zredstr: `redmapper.RedSequenceColorPar`
           Red-sequence structure
        maxchi: `float`, optional
           Maximum chi2 possible.  Default is 20.0
        ab_use: `np.array`, optional
           Integer array of indices for galaxies to use in afterburner
           Required only if running in afterburner mode
        """
        self._nodes = np.atleast_1d(nodes)
        self._corrnodes = np.atleast_1d(corrnodes)

        self._n_nodes = self._nodes.size
        self._n_corrnodes = self._corrnodes.size

        self._z = np.atleast_1d(z)
        self._z_err = np.atleast_1d(z_err)
        self._zredmagic = self._z.copy()
        self._zredmagic_e = self._z_err.copy()
        self._chisq = np.atleast_1d(chisq)
        self._mstar = np.atleast_1d(mstar)
        self._zcal = np.atleast_1d(zcal)
        self._zcal_err = np.atleast_1d(zcal_err)
        self._refmag = np.atleast_1d(refmag)
        self._randomn = np.atleast_1d(randomn)
        self._zmax = np.atleast_1d(zmax)
        self._etamin = etamin
        self._n0 = n0
        self._volume = volume
        self._zrange = zrange
        self._zbinsize = zbinsize
        self._zredstr = zredstr
        self._ab_use = ab_use

        self._maxchi = maxchi
        self._afterburner = False

        if self._z.size != self._z_err.size:
            raise ValueError("Number of z must be equal to z_err")
        if self._z.size != self._chisq.size:
            raise ValueError("Number of z must be equal to chisq")
        if self._z.size != self._mstar.size:
            raise ValueError("Number of z must be equal to mstar")
        if self._z.size != self._zcal.size:
            raise ValueError("Number of z must be equal to zcal")
        if self._z.size != self._zcal_err.size:
            raise ValueError("Number of z must be equal to zcal_err")
        if self._z.size != self._refmag.size:
            raise ValueError("Number of z must be equal to refmag")
        if self._z.size != self._randomn.size:
            raise ValueError("Number of z must be equal to randomn")
        if len(self._zrange) != 2:
            raise ValueError("zrange must have 2 elements")

    def fit(self, p0_cval, biaspars=None, eratiopars=None, afterburner=False):
        """
        Fit the redMaGiC parameters.

        Parameters
        ----------
        p0_cval: `np.array`
           Float array of starting values for chi2(z) at nodes
        biaspars: `np.array`, optional
           Parameters of afterburner bias.  Default is None
        eratiopars: `np.array`, optional
           Parameters of afterburner eratio.  Default is None
        afterburner: `bool`, optional
           Run the afterburner?  Default is False.

        Returns
        -------
        pars: `np.array`
           Float array of best-fit chi2(z) parameters
        """

        if self._ab_use is None and afterburner:
            raise RuntimeError("Must set afterburner_use if using the afterburner")

        self._afterburner = afterburner
        if afterburner:
            if biaspars is None or eratiopars is None:
                raise RuntimeError("Must set biaspars, eratiopars if using the afterburner")

            # Set _zredmagic based on the afterburner values
            spl = CubicSpline(self._corrnodes, biaspars)
            self._zredmagic = self._z - spl(self._z)

            spl = CubicSpline(self._corrnodes, eratiopars)
            self._zredmagic_e = self._z_err * spl(self._z)
        else:
            self._zredmagic[:] = self._z
            self._zredmagic_e[:] = self._z_err

        # Compute here...
        self._mstar = self._zredstr.mstar(self._zredmagic)

        pars = scipy.optimize.fmin(self, p0_cval, disp=False, xtol=1e-8, ftol=1e-8)

        return pars

    def fit_bias_eratio(self, cval, p0_bias, p0_eratio):
        """
        Fit the bias and eratio afterburner parameters.

        Parameters
        ----------
        cval: `np.array`
           Float array of chi2(z) parameters
        p0_bias: `np.array`
           Float array of initial guess of bias parameters.
        p0_eratio: `np.array`
           Float array of initial guess of eratio parameters

        Returns
        -------
        pars_bias: `np.array`
           Float array of best-fit bias parameters
        pars_eratio: `np.array`
           Float array of best-fit eratio parameters
        """

        spl = CubicSpline(self._nodes, cval)
        chi2max = np.clip(spl(self._z), 0.1, self._maxchi)

        ab_mask = ((self._chisq[self._ab_use] < chi2max[self._ab_use]) &
                   (self._refmag[self._ab_use] < (self._mstar[self._ab_use] - 2.5 * np.log10(self._etamin))))
        if self._zmax.size > 1:
            # This is an array of zmax values
            ab_mask &= (self._zredmagic[self._ab_use] < self._zmax[self._ab_use])
        else:
            # This is a single value
            ab_mask &= (self._zredmagic[self._ab_use] < self._zmax)

        ab_gd, = np.where(ab_mask)

        ab_gd = self._ab_use[ab_gd]

        mzfitter = MedZFitter(self._corrnodes, self._z[ab_gd], self._z[ab_gd] - self._zcal[ab_gd])
        pars_bias = mzfitter.fit(p0_bias, min_val=-0.1, max_val=0.1)

        y = 1.4826 * np.abs(self._z[ab_gd] - self._zcal[ab_gd]) / self._z_err[ab_gd]
        efitter = MedZFitter(self._corrnodes, self._z[ab_gd], y)
        pars_eratio = efitter.fit(p0_eratio, min_val=0.5, max_val=1.5)

        return pars_bias, pars_eratio

    def __call__(self, pars):
        """
        Call the cost function.

        Parameters
        ----------
        pars: `np.array`
           Float array of parameters

        Returns
        -------
        t: `float`
           Cost-function at pars
        """

        # chi2max is computed at the raw redshift
        spl = CubicSpline(self._nodes, pars)
        chi2max = np.clip(spl(self._z), 0.1, self._maxchi)

        zsamp = self._randomn * self._zredmagic_e + self._zredmagic

        gd, = np.where((self._chisq < chi2max) &
                       (self._refmag < (self._mstar - 2.5 * np.log10(self._etamin))) &
                       (self._zredmagic < self._zmax))

        if gd.size == 0:
            return 1e11

        # Histogram the galaxies into bins
        # This is done with the sampled redshifts
        h = esutil.stat.histogram(zsamp[gd],
                                  min=self._zrange[0], max=self._zrange[1] - 0.0001,
                                  binsize=self._zbinsize)

        # Compute density and error
        den = h.astype(np.float64) / self._volume
        den_err = np.sqrt(self._n0 * 1e-4 * self._volume) / self._volume

        # Compute cost function
        t = np.sum(((den - self._n0 * 1e-4) / den_err)**2.)

        return t

class RedmagicCalibrator(object):
    """
    Class to calibration the redMaGiC parmaeters.
    """
    def __init__(self, conf):
        """
        Instantiate a RedmagicCalibrator

        Parameters
        ----------
        conf: `redmapper.Configuration` or `str
           Configuration object or config filename
        """
        if not isinstance(conf, Configuration):
            self.config = Configuration(conf)
        else:
            self.config = conf

    def run(self, gals=None, do_run=True):
        """
        Run the redMaGiC calibration

        Parameters
        ----------
        gals: `redmapper.GalaxyCatalog`, optional
           Galaxy catalog to calibrate.  Default is None.  Used for testing.
        do_run: `bool`, optional
           Do the full run after calibration.  Default is True.
        """
        import matplotlib.pyplot as plt

        # set gals for testing purposes...

        if gals is None:
            # make sure that we have pixelized file, zreds, etc.
            if not self.config.galfile_pixelized:
                raise RuntimeError("Code only runs with pixelized galfile.")

            if self.config.zredfile is None or not os.path.isfile(self.config.zredfile):
                raise RuntimeError("Must have zreds available.")

            if self.config.catfile is None or not os.path.isfile(self.config.catfile):
                raise RuntimeError("Must have a cluster catalog available.")

        # this is the number of calibration runs we have
        nruns = len(self.config.redmagic_etas)

        # check for vlim files

        vlim_masks = OrderedDict()
        vlim_areas = OrderedDict()
        if self.config.depthfile is None or not os.path.isfile(self.config.depthfile):
            # If there is no depthfile, there are no proper vlim files...
            # So we're going to make temporary masks

            for i, vlim_lstar in enumerate(self.config.redmagic_etas):
                self.config.logger.info("Simulating volume-limit mask for %.2f" % (vlim_lstar))
                vlim_masks[self.config.redmagic_names[i]] = VolumeLimitMaskFixed(self.config)
                vlim_areas[self.config.redmagic_names[i]] = vlim_masks[self.config.redmagic_names[i]].get_areas()
        else:
            # There is a depth file so we can create/read vlim masks.
            for i, vlim_lstar in enumerate(self.config.redmagic_etas):
                self.config.logger.info("Reading/creating volume-limit mask for %.2f" % (vlim_lstar))
                vlim_masks[self.config.redmagic_names[i]] = VolumeLimitMask(self.config, vlim_lstar)
                vlim_areas[self.config.redmagic_names[i]] = vlim_masks[self.config.redmagic_names[i]].get_areas()

        # Note that the area is already scaled properly!

        if gals is None:
            # Read in galaxies with zreds
            gals = GalaxyCatalog.from_galfile(self.config.galfile,
                                              nside=self.config.d.nside,
                                              hpix=self.config.d.hpix,
                                              border=self.config.border,
                                              zredfile=self.config.zredfile,
                                              truth=self.config.redmagic_mock_truthspec)

        # Add redmagic fields
        gals.add_fields([('zuse', 'f4'),
                         ('zuse_e', 'f4'),
                         ('zspec', 'f4'),
                         ('zcal', 'f4'),
                         ('zcal_e', 'f4'),
                         ('zredmagic', 'f4'),
                         ('zredmagic_e', 'f4')])

        gals.zuse = gals.zred_uncorr
        gals.zuse_e = gals.zred_uncorr_e

        zredstr = RedSequenceColorPar(self.config.parfile, fine=True)

        mstar_init = zredstr.mstar(gals.zuse)

        # modify zrange for even bins, including cushion
        corr_zrange = np.copy(self.config.redmagic_zrange)
        nbin = np.ceil((corr_zrange[1] - corr_zrange[0]) / self.config.redmagic_calib_zbinsize).astype(np.int32)
        corr_zrange[1] = nbin * self.config.redmagic_calib_zbinsize + corr_zrange[0]

        lstar_cushion = 0.05
        z_cushion = 0.05

        # Cut input galaxies
        cut_zrange = [corr_zrange[0] - z_cushion, corr_zrange[1] + z_cushion]
        minlstar = np.clip(np.min(self.config.redmagic_etas) - lstar_cushion, 0.1, None)

        use, = np.where((gals.zuse > cut_zrange[0]) & (gals.zuse < cut_zrange[1]) &
                        (gals.chisq < self.config.redmagic_calib_chisqcut) &
                        (gals.refmag < (mstar_init - 2.5*np.log10(minlstar))))

        if use.size == 0:
            raise RuntimeError("No galaxies in redshift range/chisq range/eta range.")

        # This selects all *possible* redmagic galaxies
        gals = gals[use]
        mstar_init = mstar_init[use]

        # Run the galaxy cleaner
        # FIXME: implement cleaner

        # match to spectra
        if not self.config.redmagic_mock_truthspec:
            self.config.logger.info("Reading and matching spectra...")

            spec = Catalog.from_fits_file(self.config.specfile)
            use, = np.where(spec.z_err < 0.001)
            spec = spec[use]

            i0, i1, dists = gals.match_many(spec.ra, spec.dec, 3./3600., maxmatch=1)
            gals.zspec[i1] = spec.z[i0]
        else:
            self.config.logger.info("Using truth spectra for reference...")
            gals.zspec = gals.ztrue

        # Match to cluster catalog

        cat = Catalog.from_fits_file(self.config.catfile)
        mem = read_members(self.config.catfile)

        mem.add_fields([('z_err', 'f4')])

        a, b = esutil.numpy_util.match(cat.mem_match_id, mem.mem_match_id)
        mem.z_err[b] = cat.z_lambda_e[a]

        use, = np.where(mem.p > self.config.calib_corr_pcut)
        mem = mem[use]

        gals.zcal[:] = -1.0
        gals.zcal_e[:] = -1.0

        a, b = esutil.numpy_util.match(gals.id, mem.id)
        gals.zcal[a] = mem.z[b]
        gals.zcal_e[a] = mem.z_err[b]

        # Hack this...
        #i0, i1, dist = gals.match_many(mem.ra, mem.dec, 1./3600., maxmatch=1)
        #gals.zcal[i1] = mem.z[i0]
        #gals.zcal_e[i1] = mem.z_err[i0]

        # Clear out members
        mem = None

        self.config.redmagicfile = self.config.redmapper_filename('redmagic_calib')

        # loop over cuts...

        for i in range(nruns):
            self.config.logger.info("Working on %s: etamin = %.3f, n0 = %.3f" % (self.config.redmagic_names[i], self.config.redmagic_etas[i], self.config.redmagic_n0s[i]))

            redmagic_zrange = [self.config.redmagic_zrange[0],
                               self.config.redmagic_zmaxes[i]]

            corr_zrange = copy.copy(redmagic_zrange)
            nbin = np.ceil((corr_zrange[1] - corr_zrange[0]) / self.config.redmagic_calib_zbinsize).astype(np.int32)
            corr_zrange[1] = nbin * self.config.redmagic_calib_zbinsize + corr_zrange[0]

            # Compute the nodes
            nodes = make_nodes(redmagic_zrange, self.config.redmagic_calib_nodesize)
            corrnodes = make_nodes(redmagic_zrange, self.config.redmagic_calib_corr_nodesize)

            # Prepare calibration structure
            vmaskfile = ''
            if isinstance(vlim_masks[self.config.redmagic_names[i]], VolumeLimitMask):
                vmaskfile = vlim_masks[self.config.redmagic_names[i]].vlimfile

            calstr = Entry(np.zeros(1, dtype=[('zrange', 'f4', 2),
                                              ('corr_zrange', 'f4', 2),
                                              ('lstar_cushion', 'f4'),
                                              ('z_cushion', 'f4'),
                                              ('name', 'a%d' % (len(self.config.redmagic_names[i]) + 1)),
                                              ('maxchi', 'f4'),
                                              ('nodes', 'f8', nodes.size),
                                              ('etamin', 'f8'),
                                              ('n0', 'f8'),
                                              ('cmax', 'f8', nodes.size),
                                              ('corrnodes', 'f8', corrnodes.size),
                                              ('run_afterburner', 'i2'),
                                              ('bias', 'f8', corrnodes.size),
                                              ('eratio', 'f8', corrnodes.size),
                                              ('vmaskfile', 'a%d' % (len(vmaskfile) + 1))]))

            calstr.zrange[:] = redmagic_zrange
            calstr.corr_zrange[:] = corr_zrange
            calstr.lstar_cushion = lstar_cushion
            calstr.z_cushion = z_cushion
            calstr.name = self.config.redmagic_names[i]
            calstr.maxchi = self.config.redmagic_calib_chisqcut
            calstr.nodes[:] = nodes
            calstr.corrnodes[:] = corrnodes
            calstr.etamin = self.config.redmagic_etas[i]
            calstr.n0 = self.config.redmagic_n0s[i]
            calstr.vmaskfile = vmaskfile
            calstr.run_afterburner = self.config.redmagic_run_afterburner

            # Initial histogram
            h = esutil.stat.histogram(gals.zuse,
                                      min=corr_zrange[0], max=corr_zrange[1] - 0.0001,
                                      binsize=self.config.redmagic_calib_zbinsize)
            zbins = np.arange(h.size, dtype=np.float64) * self.config.redmagic_calib_zbinsize + corr_zrange[0] + self.config.redmagic_calib_zbinsize / 2.

            etamin_ref = np.clip(self.config.redmagic_etas[i] - lstar_cushion, 0.1, None)

            # These are possible redmagic galaxies for this selection
            red_poss, = np.where(gals.refmag < (mstar_init - 2.5*np.log10(etamin_ref)))

            # Determine which of the galaxies to use in the afterburner
            gd, = np.where(gals.zcal[red_poss] > 0.0)
            ntrain = int(self.config.redmagic_calib_fractrain * gd.size)
            r = np.random.random(gd.size)
            st = np.argsort(r)
            afterburner_use = gd[st[0: ntrain]]

            # Compute the volume
            vmask = vlim_masks[self.config.redmagic_names[i]]
            astr = vlim_areas[self.config.redmagic_names[i]]

            aind = np.searchsorted(astr.z, zbins)
            z_areas = astr.area[aind]

            volume = np.zeros(zbins.size)
            for j in xrange(zbins.size):
                volume[j] = (self.config.cosmo.V(zbins[j] - self.config.redmagic_calib_zbinsize/2.,
                                                 zbins[j] + self.config.redmagic_calib_zbinsize/2.) *
                                  (z_areas[j] / 41252.961))

            zmax = vmask.calc_zmax(gals.ra[red_poss], gals.dec[red_poss])

            if self.config.redmagic_calib_pz_integrate:
                randomn = np.random.normal(size=red_poss.size)
            else:
                randomn = np.zeros(red_poss.size)

            zsamp = randomn * gals.zuse_e[red_poss] + gals.zuse[red_poss]

            h = esutil.stat.histogram(zsamp,
                                      min=corr_zrange[0], max=corr_zrange[1] - 0.0001,
                                      binsize=self.config.redmagic_calib_zbinsize)
            dens = h.astype(np.float64) / volume

            bad, = np.where(dens < self.config.redmagic_n0s[i] * 1e-4)
            if bad.size > 0:
                self.config.logger.info("Warning: not enough galaxies at z=%s" % (zbins[bad].__str__()))

            # get starting values
            cmaxvals = np.zeros(nodes.size)

            aind = np.searchsorted(astr.z, nodes)
            test_areas = astr.area[aind]

            test_vol = np.zeros(nodes.size)
            for j in xrange(nodes.size):
                test_vol[j] = (self.config.cosmo.V(nodes[j] - self.config.redmagic_calib_zbinsize/2.,
                                                   nodes[j] + self.config.redmagic_calib_zbinsize/2.) *
                               (test_areas[j] / 41252.961))

            for j in xrange(nodes.size):
                zrange = [nodes[j] - self.config.redmagic_calib_zbinsize/2.,
                          nodes[j] + self.config.redmagic_calib_zbinsize/2.]
                if j == 0:
                    zrange = [nodes[j],
                              nodes[j] + self.config.redmagic_calib_zbinsize]
                elif j == nodes.size - 1:
                    zrange = [nodes[j] - self.config.redmagic_calib_zbinsize,
                              nodes[j]]

                #u, = np.where((zsamp > zrange[0]) & (zsamp < zrange[1]))
                u, = np.where((gals.zuse[red_poss] > zrange[0]) &
                              (gals.zuse[red_poss] < zrange[1]))

                st = np.argsort(gals.chisq[red_poss[u]])
                test_den = np.arange(u.size) / test_vol[j]
                ind = np.searchsorted(test_den, self.config.redmagic_n0s[i] * 1e-4)
                cmaxvals[j] = gals.chisq[red_poss[u[st[ind]]]]

            # minimize chisquared parameters
            rmfitter = RedmagicParameterFitter(nodes, corrnodes,
                                               gals.zuse[red_poss], gals.zuse_e[red_poss],
                                               gals.chisq[red_poss], mstar_init[red_poss],
                                               gals.zcal[red_poss], gals.zcal_e[red_poss],
                                               gals.refmag[red_poss], randomn,
                                               zmax,
                                               self.config.redmagic_etas[i],
                                               self.config.redmagic_n0s[i],
                                               volume, corr_zrange,
                                               self.config.redmagic_calib_zbinsize,
                                               zredstr,
                                               maxchi=self.config.redmagic_calib_chisqcut,
                                               ab_use=afterburner_use)

            self.config.logger.info("Fitting first pass...")
            cmaxvals = rmfitter.fit(cmaxvals, afterburner=False)

            # default is no bias or eratio
            biasvals = np.zeros(corrnodes.size)
            eratiovals = np.ones(corrnodes.size)

            # run with afterburner
            if self.config.redmagic_run_afterburner:
                self.config.logger.info("Fitting with afterburner...")

                # Let's look at 5 iterations here...
                for k in xrange(5):
                    self.config.logger.info("Afterburner iteration %d" % (k))
                    # Fit the bias and eratio...
                    biasvals, eratiovals = rmfitter.fit_bias_eratio(cmaxvals, biasvals, eratiovals)
                    cmaxvals = rmfitter.fit(cmaxvals, biaspars=biasvals, eratiopars=eratiovals, afterburner=True)

                # And a last fit of bias/eratio
                biasvals, eratiovals = rmfitter.fit_bias_eratio(cmaxvals, biasvals, eratiovals)

            rmfitter = None

            # Record the calibrations
            calstr.cmax[:] = cmaxvals
            calstr.bias[:] = biasvals
            calstr.eratio[:] = eratiovals

            calstr.to_fits_file(self.config.redmagicfile, clobber=False, extname=self.config.redmagic_names[i])

            # Do the redmagic selection on our calibration galaxies
            selector = RedmagicSelector(self.config, vlim_masks=vlim_masks)
            redgals, gd = selector.select_redmagic_galaxies(gals, self.config.redmagic_names[i],
                                                            return_indices=True)
            gals.zredmagic[gd] = redgals.zredmagic
            gals.zredmagic_e[gd] = redgals.zredmagic_e

            # make pretty plots
            nzplot = NzPlot(self.config, binsize=self.config.redmagic_calib_zbinsize)
            nzplot.plot_redmagic_catalog(gals[gd], calstr.name, calstr.etamin, calstr.n0,
                                         vlim_areas[self.config.redmagic_names[i]],
                                         zrange=corr_zrange,
                                         sample=self.config.redmagic_calib_pz_integrate,
                                         extraname='calib')

            # This stringification can be streamlined, I think.

            specplot = SpecPlot(self.config)

            # spectroscopic comparison
            okspec, = np.where(gals.zspec[gd] > 0.0)
            if okspec.size > 10:
                fig = specplot.plot_values(gals.zspec[gd[okspec]], gals.zredmagic[gd[okspec]],
                                           gals.zredmagic_e[gd[okspec]],
                                           name='z_{\mathrm{redmagic}}',
                                           title='%s: %3.1f-%02d' %
                                           (calstr.name, calstr.etamin, int(calstr.n0)),
                                           figure_return=True)
                fig.savefig(self.config.redmapper_filename('redmagic_calib_zspec_%s_%3.1f-%02d' %
                                                           (calstr.name, calstr.etamin,
                                                            int(calstr.n0)),
                                                           paths=(self.config.plotpath,),
                                                           filetype='png'))
                plt.close(fig)

            okcal, = np.where(gals.zcal[gd] > 0.0)
            if okcal.size > 10:
                fig = specplot.plot_values(gals.zcal[gd[okcal]], gals.zredmagic[gd[okcal]],
                                           gals.zredmagic_e[gd[okcal]],
                                           name='z_{\mathrm{redmagic}}',
                                           specname='z_{\mathrm{cal}}',
                                           title='%s: %3.1f-%02d' %
                                           (calstr.name, calstr.etamin, int(calstr.n0)),
                                           figure_return=True)
                fig.savefig(self.config.redmapper_filename('redmagic_calib_zcal_%s_%3.1f-%02d' %
                                                           (calstr.name, calstr.etamin,
                                                            int(calstr.n0)),
                                                           paths=(self.config.plotpath,),
                                                           filetype='png'))
                plt.close(fig)


        # Output config file here!
        runfile = self.config.configfile.replace('cal', 'run')
        if runfile == self.config.configfile:
            # We didn't find anything to replace
            parts = self.config.configfile.split('.y')
            runfile = parts[0] + '_run.yaml'

        self.runfile = os.path.join(self.config.outpath, runfile)

        # Reset the pixel to do the full sky when we create a catalog.
        self.config.hpix = 0
        self.config.nside = 0
        self.config.area = None
        self.config.output_yaml(self.runfile)

        if do_run:
            # Call the redmagic runner here.
            run_redmagic = RunRedmagicTask(runfile)
            run_redmagic.run()