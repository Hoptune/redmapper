"""Class to run redmagic, scanning over all pixels"""

from __future__ import division, absolute_import, print_function

import os
import numpy as np
import glob
import fitsio

from ..configuration import Configuration
from .redmagic_selector import RedmagicSelector
from ..catalog import Entry
from ..galaxy import GalaxyCatalog
from ..plotting import SpecPlot, NzPlot

class RunRedmagicTask(object):
    """
    Class to run redmagic on a full catalog.
    """

    def __init__(self, configfile, path=None):
        """
        Instantiate a RunRedmagicTask

        Parameters
        ----------
        configfile: `str`
           Configuration yaml filename
        path: `str`, optional
l           Output path.  Default is None, use same absolute
           path as configfile.
        """
        if path is None:
            outpath = os.path.dirname(os.path.abspath(configfile))
        else:
            outpath = path

        self.config = Configuration(configfile, outpath=path)

    def run(self, modes=None, clobber=False, do_plots=True):
        """
        Run redMaGiC selection over a full catalog.

        The modes are optional, if not specified all the modes
        will be run.

        Parameters
        ----------
        modes: `list`, optional
           List of strings of modes to run
        clobber: `bool`, optional
           Overwrite any existing files.  Default is False.
        do_plots: `bool`, optional
           Make the output plots
        """

        if not self.config.galfile_pixelized:
            raise ValueError("Code only runs with pixelized galfile.")

        # Prepare the redMaGiC selector
        selector = RedmagicSelector(self.config)

        if modes is None:
            modes = selector.modes

        n_modes = len(modes)

        # Check if files exist, clobber is False
        filenames = [''] * n_modes
        for i, mode in enumerate(modes):
            filenames[i] = self.config.redmapper_filename('redmagic_%s' % (mode))

            if os.path.isfile(filenames[i]) and not clobber:
                raise RuntimeError("redMaGiC file %s already exists, and clobber is False" % (filenames[i]))

        # Loop over all pixels in the galaxy table
        tab = Entry.from_fits_file(self.config.galfile)

        started = [False] * n_modes

        self.config.logger.info("Making redMaGiC selection for %d modes and %d pixels" % (n_modes, tab.hpix.size))

        for i, pix in enumerate(tab.hpix):
            gals = GalaxyCatalog.from_galfile(self.config.galfile,
                                              zredfile=self.config.zredfile,
                                              nside=tab.nside,
                                              hpix=pix,
                                              border=0.0,
                                              truth=True)

            # Loop over all modes
            for j, mode in enumerate(modes):
                # Select the red galaxies
                red_gals = selector.select_redmagic_galaxies(gals, mode)

                # Spool out redMaGiC galaxies
                if not started[j]:
                    # write a new file (and overwrite if necessary, since we
                    # already did the clobber check)
                    red_gals.to_fits_file(filenames[j], clobber=True)
                    started[j] = True
                else:
                    with fitsio.FITS(filenames[j], mode='rw') as fits:
                        fits[1].append(red_gals._ndarray)

        # Load in catalogs and make plots!
        if do_plots:
            import matplotlib.pyplot as plt

            for j, mode in enumerate(modes):
                gals = GalaxyCatalog.from_fits_file(filenames[j])

                nzplot = NzPlot(self.config, binsize=self.config.redmagic_calib_zbinsize)
                nzplot.plot_redmagic_catalog(gals, mode, selector.calib_data[mode].etamin,
                                             selector.calib_data[mode].n0,
                                             selector.vlim_masks[mode].get_areas(),
                                             zrange=selector.calib_data[mode].zrange,
                                             sample=self.config.redmagic_calib_pz_integrate)

                okspec, = np.where(gals.zspec > 0.0)
                if okspec.size > 0:
                    specplot = SpecPlot(self.config)
                    fig = specplot.plot_values(gals.zspec[okspec], gals.zredmagic[okspec],
                                               gals.zredmagic_e[okspec],
                                               name='z_{\mathrm{redmagic}}',
                                               title='%s: %3.1f-%02d' %
                                               (mode, selector.calib_data[mode].etamin,
                                                int(selector.calib_data[mode].n0)),
                                               figure_return=True)
                    fig.savefig(self.config.redmapper_filename('redmagic_zspec_%s_%3.1f-%02d' %
                                                               (mode, selector.calib_data[mode].etamin,
                                                                int(selector.calib_data[mode].n0)),
                                                               paths=(self.config.plotpath,),
                                                               filetype='png'))
                    plt.close(fig)

        # All done!  (Except for randoms, tbd...)